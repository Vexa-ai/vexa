---
services: [meeting-api, api-gateway, admin-api]
tests3:
  targets: [webhooks, smoke]
  checks: [DB_POOL_NO_EXHAUSTION]
---

# Webhooks

## Why

External systems need to react to meeting lifecycle events (bot joined, status changed, transcription ready, meeting ended) without polling. Vexa pushes events to user-configured URLs with HMAC-signed payloads and durable retry.

## What

```
User sets webhook_url + events via PUT /user/webhook (admin-api)
       │
       ▼
Bot creation (POST /bots) → api-gateway validates token → admin-api /internal/validate
       │                                                     returns {user_id, scopes,
       │                                                              webhook_url,
       │                                                              webhook_secret,
       │                                                              webhook_events}
       ▼
api-gateway injects X-User-Webhook-* headers → meeting-api stores in meeting.data
       │
       ▼
Meeting lifecycle event → meeting-api fires webhook → POST to user's URL
       │                                              (HMAC signed, retried on failure)
       ▼
Delivery status recorded in meeting.data.{webhook_delivery | webhook_deliveries}
```

### Components

| Component | File | Role |
|-----------|------|------|
| User webhook config | `services/admin-api/app/main.py:set_user_webhook` | Stores `webhook_url`, `webhook_secret`, `webhook_events` in `User.data` |
| Token validation | `services/admin-api/app/main.py:validate_token` | Returns webhook fields to gateway |
| Gateway injection | `services/api-gateway/main.py:forward_request` | Strips client-supplied `X-User-Webhook-*`, injects validated values |
| Meeting data capture | `services/meeting-api/meeting_api/meetings.py` | Reads headers at bot creation, persists into `meeting.data` |
| Status webhooks | `services/meeting-api/meeting_api/webhooks.py:send_status_webhook` | Fires on every transition that passes `_is_event_enabled` |
| Completion webhook | `services/meeting-api/meeting_api/webhooks.py:send_completion_webhook` | Fires unconditionally on `meeting.completed` from `run_all_tasks` |
| Event webhook | `services/meeting-api/meeting_api/webhooks.py:send_event_webhook` | Recording/transcription ready events |
| Delivery + retry | `services/meeting-api/meeting_api/webhook_delivery.py` | HMAC sign + HTTP POST + Redis retry queue |
| Retry worker | `services/meeting-api/meeting_api/webhook_retry_worker.py` | Exponential backoff: 1m → 5m → 30m → 2h (24h max age) |
| Internal hooks | `services/meeting-api/meeting_api/post_meeting.py:fire_post_meeting_hooks` | Server-side `POST_MEETING_HOOKS` for billing/analytics |

## Expected Behavior

### Event catalog

| Event type | Fires on | Enabled by default | Delivery tracking |
|------------|----------|--------------------|-------------------|
| `meeting.completed` | Meeting status → `completed` (via `run_all_tasks` in post-meeting flow) | **yes** | `meeting.data.webhook_delivery` |
| `meeting.started` | Meeting status → `active` | no — requires `webhook_events["meeting.started"] = true` | `meeting.data.webhook_deliveries[]` (bounded list, 20 entries) |
| `bot.failed` | Meeting status → `failed` | no — requires `webhook_events["bot.failed"] = true` | `meeting.data.webhook_deliveries[]` |
| `meeting.status_change` | Any other status transition (joining, awaiting_admission, stopping, needs_human_help) | no — requires `webhook_events["meeting.status_change"] = true` | `meeting.data.webhook_deliveries[]` |
| `recording.ready` | Recording finalized (fire-and-forget) | no | not tracked |
| `transcription.ready` | Transcription segment pipeline completion | no | not tracked |

**Status transition sources that fire webhooks** (all gated by `_is_event_enabled`):
- Bot container callbacks (`/bots/internal/callback/status_change`, `/started`, `/joining`, `/awaiting_admission`, `/exited`)
- User-initiated stop (`DELETE /bots/...` → `stopping`)
- Scheduler timeout (`max_bot_time_exceeded` → `stopping`)

### Payload envelope (frozen contract)

```json
{
  "event_id": "evt_<uuid>",
  "event_type": "meeting.started",
  "api_version": "2026-03-01",
  "created_at": "2026-04-17T09:13:54.161894+00:00",
  "data": {
    "meeting": {
      "id": 137,
      "user_id": 5,
      "platform": "google_meet",
      "native_meeting_id": "abc-defg-hij",
      "constructed_meeting_url": "https://meet.google.com/abc-defg-hij",
      "status": "active",
      "start_time": "2026-04-17T09:13:54.161894+00:00",
      "end_time": null,
      "data": { /* cleaned meeting.data, no internal fields */ },
      "created_at": "...",
      "updated_at": "..."
    },
    "status_change": {
      "from": "requested",
      "to": "active",
      "reason": null,
      "timestamp": "2026-04-17T09:13:54.161894",
      "transition_source": "bot_callback"
    }
  }
}
```

**Internal fields stripped from `data.meeting.data`**: `webhook_delivery`, `webhook_deliveries`, `webhook_secret`, `webhook_secrets`, `webhook_events`, `webhook_url`, `bot_container_id`, `container_name`.

### Request headers

| Header | Set when | Value |
|--------|----------|-------|
| `Content-Type` | always | `application/json` |
| `Authorization` | webhook_secret configured | `Bearer <secret>` (backward compat) |
| `X-Webhook-Signature` | webhook_secret configured | `sha256=<hex>` — HMAC over `<unix_ts>.<payload_bytes>` |
| `X-Webhook-Timestamp` | webhook_secret configured | unix timestamp (replay-protection window) |

### Delivery semantics

1. **First attempt**: synchronous, up to 3 retries with exponential backoff (1s, 2s, 4s).
2. **5xx or 429**: retry. **4xx (not 429)**: drop immediately, no retry.
3. **Total sync failure + Redis available**: persist to `webhook:retry_queue` for background worker.
4. **Background retries**: 1 min → 5 min → 30 min → 2 h. Drop after 24 h total age.
5. **Delivery status**:
   - `meeting.data.webhook_delivery` — single record for `meeting.completed` (status: delivered/queued/failed, status_code, attempts, timestamp).
   - `meeting.data.webhook_deliveries[]` — bounded list (max 20) of status-change webhook attempts.

### Configuration API

**Set webhook** (authenticated, user API key):
```bash
PUT /user/webhook
{
  "webhook_url": "https://your-server.com/webhooks/vexa",
  "webhook_secret": "whsec_...",          # optional, auto-signs when present
  "webhook_events": {                       # optional, map of event_type → enabled
    "meeting.completed": true,
    "meeting.started": true,
    "meeting.status_change": true,
    "bot.failed": true
  }
}
```

Omitting `webhook_events` preserves existing config. Setting it to `null` clears it. Omitting `webhook_secret` preserves existing.

**Get webhook**: `GET /user/webhook` — returns current config (secret masked).

**Rotate secret**: `POST /user/webhook/rotate` — generates new `whsec_...`, returns it once.

### Security

1. **Client-supplied `X-User-Webhook-*` headers are always stripped** by the gateway before any forwarding — prevents webhook URL spoofing attacks.
2. **`webhook_secret` is never returned in API responses** — `MeetingResponse` field serializer strips it; `/bots/status` and `/bots/{id}` responses use `safe_data` filter.
3. **Internal data keys stripped from webhook payloads** — `clean_meeting_data()` removes sensitive/internal fields.
4. **SSRF protection** — `validate_webhook_url()` blocks private IPs (10.0.0.0/8, 172.16/12, 192.168/16, 169.254/16), localhost, cloud metadata endpoints, internal service hostnames.
5. **Replay protection** — `X-Webhook-Timestamp` is signed together with payload.

### Collector robustness

The transcription collector uses Redis Streams with consumer groups. If Redis loses the groups (eviction, FLUSHALL, restart while meeting-api stays up), the consumer **recreates the group on `NOGROUP` error** instead of failing silently. Verified in `services/meeting-api/meeting_api/collector/consumer.py:_ensure_group`.

## How (User-facing)

### 1. Register your webhook

```bash
curl -X PUT http://localhost:8056/user/webhook \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook_url": "https://your-server.com/webhooks/vexa",
    "webhook_secret": "whsec_your_signing_secret",
    "webhook_events": {
      "meeting.completed": true,
      "meeting.started": true,
      "bot.failed": true
    }
  }'
```

### 2. Create a bot — no webhook headers needed

Gateway auto-injects your stored webhook config. Clients cannot override via headers (stripped for security).

```bash
curl -X POST http://localhost:8056/bots \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij",
    "bot_name": "Vexa Notetaker"
  }'
```

### 3. Verify incoming webhooks

```python
import hmac, hashlib, time

def verify(payload_bytes, signature_header, timestamp_header, secret, max_age=300):
    # Replay protection
    if abs(time.time() - int(timestamp_header)) > max_age:
        return False
    signed = f"{timestamp_header}.".encode() + payload_bytes
    expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

### 4. Inspect delivery status (debug)

Completion webhook:
```bash
curl -H "X-API-Key: $VEXA_API_KEY" http://localhost:8056/bots/google_meet/abc-defg-hij \
  | jq '.data.webhook_delivery'
# {"status":"delivered","status_code":200,"attempts":1,"delivered_at":"..."}
```

Status webhook history:
```bash
... | jq '.data.webhook_deliveries'
# [{"event_type":"meeting.started","status":"delivered","status_code":200,...}, ...]
```

### 5. Server-side routing (`POST_MEETING_HOOKS`)

For internal billing/analytics pipelines, set:
```bash
POST_MEETING_HOOKS=http://agent-api:8100/internal/webhooks/meeting-completed
```
Fires on every meeting completion, independent of per-user webhook URLs. Does not use the same HMAC secret (internal-only trust boundary).

## DoD

| # | Check | Weight | Status | Evidence | Last checked | Test |
|---|-------|--------|--------|----------|--------------|------|
| 1 | Gateway strips spoofed `X-User-Webhook-*` headers | 10 | PASS | `test_strips_spoofed_webhook_headers` + smoke `spoof:` check | 2026-04-17 | api-gateway unit + `tests3/tests/webhooks.sh` #4 |
| 2 | Gateway injects webhook config from validated user data | 15 | PASS | `test_valid_token_injects_webhook_headers` + smoke `inject:` check | 2026-04-17 | api-gateway unit + `tests3/tests/webhooks.sh` #3 |
| 3 | `PUT /user/webhook` accepts and persists `webhook_events` | 10 | PASS | `WebhookUpdate` schema + `set_user_webhook` handler | 2026-04-17 | admin-api unit |
| 4 | `meeting.completed` webhook delivered to user endpoint | 15 | PASS | `e2e: meeting.completed webhook delivered` | 2026-04-17 | `tests3/tests/webhooks.sh` #9 |
| 5 | Status webhooks fire when enabled via `webhook_events` | 15 | PASS | `e2e: status webhooks fired (N events: meeting.stopping, meeting.completed)` | 2026-04-17 | `tests3/tests/webhooks.sh` #9 |
| 6 | Status webhooks filtered when NOT enabled | 10 | PASS | `test_status_webhook_skips_disabled_event` | 2026-04-17 | meeting-api unit |
| 7 | Envelope shape (event_id, event_type, api_version, created_at, data) | 10 | PASS | `build_envelope()` contract test + smoke `envelope:` check | 2026-04-17 | contracts + `tests3/tests/webhooks.sh` #5 |
| 8 | HMAC signing with timestamp (replay protection) | 10 | PASS | `X-Webhook-Signature: sha256=...`, `X-Webhook-Timestamp` present; absent without secret | 2026-04-17 | contracts + `tests3/tests/webhooks.sh` #7 |
| 9 | Internal fields stripped from payload | 10 | PASS | `clean_meeting_data` strips `webhook_secret`, `webhook_url`, `bot_container_id`, etc. | 2026-04-17 | `tests3/tests/webhooks.sh` #6 |
| 10 | `webhook_secret` never in API responses | 10 | PASS | `MeetingResponse.field_serializer` + `safe_data` filter; smoke `no leak:` check | 2026-04-17 | `tests3/tests/webhooks.sh` #8 |
| 11 | Failed deliveries persist to Redis retry queue | 10 | PASS | `deliver()` enqueues on 5xx after retries; `webhook_delivery.status=queued` | 2026-04-17 | meeting-api unit + integration |
| 12 | Retry worker backoff schedule (1m→5m→30m→2h, 24h max) | 5 | PASS | `webhook_retry_worker.py` constants verified | 2026-04-17 | meeting-api unit |
| 13 | SSRF protection blocks private/internal URLs | 10 | PASS | `validate_webhook_url()` rejects RFC1918, localhost, metadata, internal hostnames | 2026-04-17 | meeting-api unit |
| 14 | DB connection pool does not exhaust (webhook callers no longer hold session during HTTP) | 15 | PASS | `DB_POOL_NO_EXHAUSTION` contract: 10× GET /bots/status sequential, no 504 | 2026-04-17 | `tests3/checks/run` |
| 15 | Collector recovers from Redis group loss (NOGROUP) — transcripts keep persisting | 15 | PASS | `_ensure_group()` on `redis.exceptions.ResponseError` containing "NOGROUP" | 2026-04-17 | meeting-api collector + VM smoke |

Confidence: 100 (all 15 items PASS — verified on VM 2026-04-17)
