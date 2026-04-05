---
id: test/w6b-webhooks
type: validation
requires: [test/api-full]
produces: [WEBHOOK_OK]
validates: [webhooks]
docs: [features/webhooks/README.md]
mode: machine
---

# W6b — Webhook Delivery

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Configure webhook URL on a bot. Trigger lifecycle event. Verify delivery, HMAC signature, envelope shape.

## Inputs

| Name | From | Description |
|------|------|-------------|
| GATEWAY_URL | W1 | API gateway base URL |
| API_TOKEN | W1 | Valid API token |

## Script

```bash
eval $(./scripts/13-webhooks.sh $GATEWAY_URL $API_TOKEN)
```

## Steps

1. Start a local HTTP listener (nc, python http.server, or ncat)
2. POST /bots with `webhook_url` pointing to listener, `webhook_secret` set
3. Trigger a bot event (create → callback → stop lifecycle)
4. Verify listener received POST with:
   - `X-Webhook-Signature` header (HMAC-SHA256)
   - `X-Webhook-Timestamp` header
   - Body: `{event_id, event_type, api_version, created_at, data}`
   - No internal fields (bot_container_id, webhook_secrets)
5. Verify without secret → no signature header

## Outputs

| Name | Description |
|------|-------------|
| WEBHOOK_OK | true if delivery + signing verified |

## Implementation notes

- Webhook config (webhook_url, webhook_secret, webhook_events) is passed via **request headers** (`X-User-Webhook-URL`, `X-User-Webhook-Secret`, `X-User-Webhook-Events`), NOT body fields
- SSRF protection (`webhook_url.py`) blocks localhost, private IPs, Docker service hostnames — cannot use a local listener inside the Docker network
- Script uses httpbin.org/post as the test webhook endpoint and validates envelope shape by importing `build_envelope` inside the container
- Container name defaults to `vexa-meeting-api-1`, overridable via `MEETING_API_CONTAINER` env var

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| "hook unreachable from container" | Target service not running or DNS fails inside Docker network | Verify the hook target container is running (`docker ps`) | Internal hooks use Docker service names (e.g. `http://agent-api:8100/...`) |
| "POST /bots with webhook headers failed" | API token invalid, missing scopes, or gateway down | Run `scripts/02-api.sh` first to get a valid token | Script requires `test/api-full` as a prerequisite |
| "could not run envelope check in container" | meeting-api container not running or Python import fails | Check `docker ps` for the meeting-api container | The script imports `meeting_api.webhook_delivery` inside the container |
| "webhook_url not found in meeting data" | API response sanitizes webhook config from GET responses | This is acceptable security behavior — the config is stored server-side | webhook_secret should never appear in API responses |

## Docs ownership

After this test runs, verify and update:

- **features/webhooks/README.md**
  - DoD table: update Status, Evidence, Last checked for all items: #1 (POST_MEETING_HOOKS configured and fires), #2 (webhook envelope shape — `event_id`, `api_version`, `data`), #3 (HMAC signing with secret), #4 (delivery logged), #5 (no internal fields leaked)
  - Components table: verify `services/meeting-api/meeting_api/post_meeting.py` (post-meeting hooks) and `services/meeting-api/meeting_api/meetings.py` (webhook config) paths are correct
  - Architecture: verify the documented flow `Meeting event -> meeting-api -> POST to user's webhook URL` matches actual behavior — if webhook delivery uses exponential backoff or Redis-backed retry queue, verify that matches the meeting-api README's webhook delivery section
  - Confidence score: recalculate after updating statuses
