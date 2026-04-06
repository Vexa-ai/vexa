---
needs: [GATEWAY_URL, API_TOKEN, DATASET_PATH, MEETING_PLATFORM, NATIVE_MEETING_ID]
gives: [DELIVERY_OK, WS_REST_MATCH, PHANTOM_COUNT]
---

use: lib/http

# RT Delivery

> **Why:** Segments pass through Redis, WebSocket, REST, and Postgres. A bug in any layer means the user sees stale, missing, or phantom data.
> **What:** Validate the delivery pipeline: monotonic confirmed count, no phantom segments, WS-delivered segments match REST.
> **How:** Replay dataset, check per-tick invariants (monotonic, no phantoms, progressive coverage), then verify WS segments exist in REST with matching segment_ids.

## state

    DELIVERY_OK   = false
    WS_REST_MATCH = 0
    PHANTOM_COUNT = 0

## steps

```
1. replay
   do: DATASET={DATASET_NAME} API_TOKEN={API_TOKEN} node delivery/replay-delivery-test.js
   expect: exits 0
   on_fail: stop

2. per_tick
   > Analyze delivery log:
   > - monotonic: confirmed count never decreases
   > - speaker correct: matches ground truth
   > - no phantoms: every confirmed segment exists in GT
   > - progressive: GT coverage increases monotonically
   expect: all checks pass
   => PHANTOM_COUNT
   on_fail: continue

3. immutability
   do: sleep 45

4. ws_rest_match
   call: http.get_json(URL="{GATEWAY_URL}/transcripts/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}", TOKEN={API_TOKEN})
   > Compare WS-delivered segments to REST by segment_id.
   => WS_REST_MATCH = matching count
   expect: all WS segments found in REST
   expect: REST count >= WS count
   on_fail: continue

5. summary
   => DELIVERY_OK = (PHANTOM_COUNT == 0 and WS_REST_MATCH > 0)
   if DELIVERY_OK: emit PASS "delivery validated"
   else: emit FAIL "delivery issues"
```
