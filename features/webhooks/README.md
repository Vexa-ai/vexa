---
services: [meeting-api, api-gateway, admin-api]
tests3:
  # Gate: release-ship blocks if this feature's confidence falls below the threshold.
  gate:
    confidence_min: 95
  # DoDs are behavioral assertions mirroring the "Expected Behavior" section below.
  # Each has an `evidence` binding to a test step (tests3/test-registry.yaml) or
  # a named check (tests3/checks/registry.json). The aggregator
  # (tests3/lib/aggregate.py) computes confidence = sum(weight for pass) / sum(weight) * 100.
  dods:
    # ── Event catalog ─────────────────────────────────────────
    - id: events-meeting-completed
      label: "meeting.completed fires on every bot exit (default-enabled)"
      weight: 10
      evidence: {test: webhooks, step: e2e_completion, modes: [compose]}
    - id: events-status-webhooks
      label: "Status-change webhooks fire when enabled via webhook_events (meeting.started / bot.failed / meeting.status_change)"
      weight: 10
      evidence: {test: webhooks, step: e2e_status, modes: [compose]}

    # ── Envelope + headers ────────────────────────────────────
    - id: envelope-shape
      label: "Every webhook carries envelope: event_id, event_type, api_version, created_at, data"
      weight: 10
      evidence: {test: webhooks, step: envelope, modes: [compose, helm]}
    - id: headers-hmac
      label: "X-Webhook-Signature = HMAC-SHA256(timestamp + '.' + payload) when secret is set"
      weight: 10
      evidence: {test: webhooks, step: hmac, modes: [compose, helm]}

    # ── Security ──────────────────────────────────────────────
    - id: security-spoof-protection
      label: "Client-supplied X-User-Webhook-* headers cannot override stored config"
      weight: 10
      evidence: {test: webhooks, step: spoof, modes: [compose, helm]}
    - id: security-secret-not-exposed
      label: "webhook_secret never appears in any API response (POST /bots, GET /bots/status)"
      weight: 10
      evidence: {test: webhooks, step: no_leak_response, modes: [compose, helm]}
    - id: security-payload-hygiene
      label: "Internal fields (secret, url, container ids, delivery state) stripped from webhook payloads"
      weight: 5
      evidence: {test: webhooks, step: no_leak_payload, modes: [compose, helm]}

    # ── Configuration flow ────────────────────────────────────
    - id: flow-user-config
      label: "PUT /user/webhook persists webhook_url + webhook_secret + webhook_events to User.data"
      weight: 10
      evidence: {test: webhooks, step: config, modes: [compose]}
    - id: flow-gateway-inject
      label: "Gateway injects validated webhook config into meeting.data on POST /bots"
      weight: 15
      evidence: {test: webhooks, step: inject, modes: [compose]}

    # ── Reliability ───────────────────────────────────────────
    - id: reliability-db-pool
      label: "DB connection pool doesn't exhaust under repeated status requests"
      weight: 10
      evidence: {check: DB_POOL_NO_EXHAUSTION, modes: [lite, compose, helm]}
---

# Webhooks

## Why

External systems need to react to meeting lifecycle events (bot joined, status changed, recording finalized, meeting ended) without polling. Vexa pushes events to user-configured URLs with HMAC-signed payloads and durable retry.

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
| Event webhook | `services/meeting-api/meeting_api/webhooks.py:send_event_webhook` | `recording.completed` events (fire-and-forget) |
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
| `recording.completed` | Recording finalized (fire-and-forget from `recordings.py`) | no — requires `webhook_events["recording.completed"] = true` | not tracked |

> **Note**: `transcription.completed` / `transcription.ready` are **not implemented**. Transcript segments become available via `GET /transcripts/...` after `meeting.completed` fires, but no dedicated transcription webhook exists yet.

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


<!-- BEGIN AUTO-DOD -->
<!-- Auto-written by tests3/lib/aggregate.py from release tag `0.10.0-260417-1408`. Do not edit by hand — edit the `tests3.dods:` frontmatter + re-run `make -C tests3 report --write-features`. -->

**Confidence: 0%** (gate: 95%, status: ❌ below gate)

| # | Behavior | Weight | Status | Evidence (modes) |
|---|----------|-------:|:------:|------------------|
| events-meeting-completed | meeting.completed fires on every bot exit (default-enabled) | 10 | ⬜ missing | `compose`: no report for test=webhooks |
| events-status-webhooks | Status-change webhooks fire when enabled via webhook_events (meeting.started / bot.failed / meeting.status_change) | 10 | ⬜ missing | `compose`: no report for test=webhooks |
| envelope-shape | Every webhook carries envelope: event_id, event_type, api_version, created_at, data | 10 | ⬜ missing | `compose`: no report for test=webhooks; `helm`: report has no step=envelope |
| headers-hmac | X-Webhook-Signature = HMAC-SHA256(timestamp + '.' + payload) when secret is set | 10 | ⬜ missing | `compose`: no report for test=webhooks; `helm`: report has no step=hmac |
| security-spoof-protection | Client-supplied X-User-Webhook-* headers cannot override stored config | 10 | ⬜ missing | `compose`: no report for test=webhooks; `helm`: report has no step=spoof |
| security-secret-not-exposed | webhook_secret never appears in any API response (POST /bots, GET /bots/status) | 10 | ⬜ missing | `compose`: no report for test=webhooks; `helm`: report has no step=no_leak_response |
| security-payload-hygiene | Internal fields (secret, url, container ids, delivery state) stripped from webhook payloads | 5 | ⬜ missing | `compose`: no report for test=webhooks; `helm`: report has no step=no_leak_payload |
| flow-user-config | PUT /user/webhook persists webhook_url + webhook_secret + webhook_events to User.data | 10 | ⬜ missing | `compose`: no report for test=webhooks |
| flow-gateway-inject | Gateway injects validated webhook config into meeting.data on POST /bots | 15 | ⬜ missing | `compose`: no report for test=webhooks |
| reliability-db-pool | DB connection pool doesn't exhaust under repeated status requests | 10 | ❌ fail | `lite`: check DB_POOL_NO_EXHAUSTION not found in any smoke-* report; `compose`: check DB_POOL_NO_EXHAUSTION not found in any smoke-* report; `helm`: smoke-contract/DB_POOL_NO_EXHAUSTION: 10/10 requests failed — likely DB pool exhaustion |

<!-- END AUTO-DOD -->


