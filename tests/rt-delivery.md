---
id: test/rt-delivery
type: validation
requires: [test/rt-replay]
produces: [DELIVERY_OK, WS_REST_MATCH, PHANTOM_COUNT]
mode: machine
---

# RT Delivery — WS/REST Consistency Validation

> Follows [RULES.md](RULES.md). This procedure owns its scripts.

Validate that confirmed segments flow correctly through Redis → WS → REST → Postgres. Check monotonic delivery, no phantoms, WS/REST consistency.

## DoD items this test owns

| Feature | # | Check |
|---------|---|-------|
| realtime-transcription | 3 | WS delivery matches REST |

## Docs this test owns

This test validates the WebSocket and REST examples in:
- [features/realtime-transcription/README.md](../features/realtime-transcription/README.md) — WS subscribe format, REST transcript endpoint, segment JSON shape

If the WS protocol, REST response format, or segment fields differ from docs, this test fixes the docs and logs FIX.

## Inputs

| Name | From | Description |
|------|------|-------------|
| GATEWAY_URL | W1 | API gateway |
| API_TOKEN | W1 | Valid token |
| DATASET_PATH | rt-replay | Path to data/core/{dataset}/ with transcript.jsonl |

## Steps

```
1  replay delivery:
     DATASET=$DATASET_NAME API_TOKEN=$TOKEN node delivery/replay-delivery-test.js

2  per-tick validation:
     - monotonic: confirmed count never decreases
     - speaker correct: each segment matches ground truth speaker
     - no phantoms: every confirmed segment exists in ground truth
     - progressive: GT coverage increases monotonically
     - pending sanity: non-empty text, valid speaker

3  after immutability (30s + 15s wait):
     - full coverage: all GT utterances matched
     - REST match: every WS segment in REST with same segment_id + data
     - REST completeness: REST has ≥ WS segments

4  report:
     DELIVERY_OK = all checks pass
     WS_REST_MATCH = count of matching WS↔REST segments
     PHANTOM_COUNT = segments in output not in ground truth
```

## Outputs

| Name | Description |
|------|-------------|
| DELIVERY_OK | true if all checks pass |
| WS_REST_MATCH | WS segments found in REST with matching data |
| PHANTOM_COUNT | Segments not in ground truth (should be 0) |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
