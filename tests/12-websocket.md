---
id: test/w6a-websocket
type: validation
requires: [test/api-full]
produces: [WEBSOCKET_OK]
validates: [realtime-transcription]
docs: [features/realtime-transcription/README.md, services/api-gateway/README.md]
mode: machine
---

# W6a — WebSocket Streaming

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Connect to gateway WebSocket. Subscribe to meeting events. Verify auth, delivery, ordering.

## Inputs

| Name | From | Description |
|------|------|-------------|
| GATEWAY_URL | W1 | API gateway base URL |
| API_TOKEN | W1 | Valid API token |
| MEETING_ID | W4 (optional) | Active meeting to subscribe to |

## Script

```bash
eval $(./scripts/12-websocket.sh $GATEWAY_URL $API_TOKEN $MEETING_ID)
```

## Steps

1. Connect `ws://$GATEWAY_URL/ws?api_key=$API_TOKEN` — verify accepted (send ping, expect pong)
2. Connect without key — verify rejected (`{"type":"error","error":"missing_api_key"}`, close 4401)
3. Send `{"action": "ping"}` — verify `{"type":"pong"}`
4. Send `{"action": "subscribe", "meetings": [{"platform": "...", "native_id": "..."}]}` — verify `{"type":"subscribed"}`. Requires resolving MEETING_ID to platform/native_id via `GET /meetings`.
5. Send `{"action": "unsubscribe", "meetings": [...]}` — verify `{"type":"unsubscribed"}`
6. Send invalid JSON — verify `{"type":"error","error":"invalid_json"}` and connection survives
7. Validate transcript segments via `GET /transcripts/{platform}/{native_meeting_id}` — check no duplicate segment_ids, all segments have text + speaker
8. Send unknown action — verify `{"type":"error","error":"unknown_action"}`

**Note:** The WS endpoint is a live pub/sub subscription (Redis channels). It does NOT replay historical segments. For completed meetings, segment validation uses the REST transcript API.

## Outputs

| Name | Description |
|------|-------------|
| WEBSOCKET_OK | true if all checks pass |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| Subscribe returns `authorization_service_error` | Transcription collector not running or not reachable | Check `docker ps` for tc service | 2026-04-05 |
| Some segments missing `speaker` | System/partial segments may lack speaker attribution | Logged as FINDING, not failure | 2026-04-05 |
| `ws` module not found in node | `wscat` installed but `ws` npm package missing globally | `npm i -g ws` or use project-local node_modules | 2026-04-05 |

## Docs ownership

After this test runs, verify and update:

- **features/realtime-transcription/README.md**
  - DoD table: update Status, Evidence, Last checked for item #3 (WS delivery matches REST) — this test validates that WebSocket-delivered segments are consistent with REST transcript API results
  - How section, step 3 (Subscribe to live transcription): verify the documented WebSocket URL `ws://$GATEWAY/ws?api_key=$TOKEN` matches the actual connection URL used by the test
  - How section: verify the subscribe action format `{"action": "subscribe", "meetings": [{"meeting_id": "..."}]}` matches the actual protocol — the test may show it uses `platform`/`native_id` instead of `meeting_id`
  - How section: verify the segment JSON shape (`segment_id`, `speaker`, `text`, `start`, `end`, `language`, `completed`, `absolute_start_time`) matches actual WebSocket messages received

- **services/api-gateway/README.md**
  - WebSocket endpoint: verify WS `/ws` with `api_key` query parameter works as documented
  - Known Limitations #5 (No WebSocket integration tests): if this test now covers subscribe -> receive round-trip, update the limitation status
  - Token validation: verify that connecting without an API key returns `{"type":"error","error":"missing_api_key"}` with close code 4401 as the test checks
  - Rate limiting: verify the documented WS tier (20/min) matches actual behavior if the test triggers rate limiting
