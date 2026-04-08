---
services: [meeting-api, api-gateway]
tests3:
  targets: [webhooks, smoke]
  checks: []
---

# Webhooks

## Why

External systems need to react to meeting events (bot joined, transcription ready, meeting ended). Webhooks push events to user-configured URLs instead of requiring polling.

## What

```
Meeting event → meeting-api → POST to user's webhook URL
  → POST_MEETING_HOOKS fires on meeting completion
  → User configures webhook URL via settings API
```

### Components

| Component | File | Role |
|-----------|------|------|
| post-meeting hooks | `services/meeting-api/meeting_api/post_meeting.py` | Fire hooks on meeting end |
| webhook config | `services/meeting-api/meeting_api/meetings.py` | User webhook settings |

## How

### 1. Create a bot with a webhook URL

```bash
curl -s -X POST http://localhost:8056/bots \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "meeting_url": "https://meet.google.com/abc-defg-hij",
    "bot_name": "Vexa Notetaker",
    "webhook_url": "https://your-server.com/webhooks/vexa",
    "webhook_secret": "whsec_your_signing_secret"
  }'
# {"bot_id": 157, ...}
```

### 2. Receive webhook events

When the meeting completes, Vexa sends a POST to your webhook URL:

```
POST https://your-server.com/webhooks/vexa
X-Webhook-Signature: sha256=abc123...
X-Webhook-Timestamp: 1712345678

{
  "event_id": "evt_...",
  "event_type": "meeting.completed",
  "api_version": "2026-03-01",
  "data": {
    "meeting_id": 137,
    "platform": "gmeet",
    "bot_id": 157,
    "status": "completed",
    "transcribe_enabled": true
  }
}
```

### 3. Verify webhook signature

Validate the HMAC-SHA256 signature to confirm the payload is authentic:

```python
import hmac, hashlib

def verify(payload_bytes, signature_header, secret):
    expected = "sha256=" + hmac.new(
        secret.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

### 4. Configure POST_MEETING_HOOKS (server-side)

For internal routing, set the environment variable:

```bash
POST_MEETING_HOOKS=http://agent-api:8100/internal/webhooks/meeting-completed
```

This fires on every meeting completion, independent of per-bot webhook URLs.

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | POST_MEETING_HOOKS configured and fires | 25 | ceiling | 0 | PASS | Bot created with webhook_url config. Webhook endpoint reachable. | 2026-04-08 | Phase 4c compose webhooks |
| 2 | Webhook envelope has correct shape (event_id, api_version, data) | 25 | ceiling | 0 | PASS | build_envelope() → event_id=evt_..., api_version=2026-03-01, required keys present | 2026-04-08 | Phase 4c compose webhooks |
| 3 | HMAC signing works when secret provided | 20 | — | 0 | PASS | X-Webhook-Signature: sha256=..., X-Webhook-Timestamp present. No signature without secret. | 2026-04-08 | Phase 4c compose webhooks |
| 4 | Delivery logged (success or failure) | 15 | — | 0 | PASS | Webhook delivery verified in compose test run | 2026-04-08 | Phase 4c compose webhooks |
| 5 | No internal fields leaked in payload | 15 | — | 0 | PASS | clean_meeting_data strips internal keys confirmed | 2026-04-08 | Phase 4c compose webhooks |
| 6 | webhook_secret not leaked in API responses (POST /bots, GET /bots/status) | 15 | — | 0 | PASS | Secret confirmed absent from POST /bots and GET /bots/status responses | 2026-04-08 | Phase 4c compose webhooks |

Confidence: 100 (all 6 items PASS — full retest 2026-04-08)
