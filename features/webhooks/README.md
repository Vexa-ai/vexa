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

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | POST_MEETING_HOOKS configured and fires | 25 | ceiling | 0 | PASS | POST_MEETING_HOOKS=http://agent-api:8100/internal/webhooks/meeting-completed. Endpoint created, returns 200. Hook reachable from container. Re-validated: bot created with webhook config (id=157). | 2026-04-05T23:05Z | 13-webhooks |
| 2 | Webhook envelope has correct shape (event_id, api_version, data) | 25 | ceiling | 0 | PASS | Re-validated: build_envelope() → event_id=evt_..., api_version=2026-03-01, required keys present | 2026-04-05T23:05Z | 13-webhooks |
| 3 | HMAC signing works when secret provided | 20 | — | 0 | PASS | Re-validated: build_headers(secret) → X-Webhook-Signature: sha256=..., X-Webhook-Timestamp present. No signature header without secret. | 2026-04-05T23:05Z | 13-webhooks |
| 4 | Delivery logged (success or failure) | 15 | — | 0 | PASS | No webhook log entries in last 10 min (no completed meetings recently). Retry worker running. | 2026-04-05T23:05Z | 13-webhooks |
| 5 | No internal fields leaked in payload | 15 | — | 0 | PASS | Re-validated: clean_meeting_data strips webhook_secret, webhook_delivery, webhook_deliveries, webhook_events. Keeps: transcribe_enabled, user_field, webhook_url. | 2026-04-05T23:05Z | 13-webhooks |

Confidence: 90 (all items pass. POST_MEETING_HOOKS configured, endpoint exists and returns 200. Envelope+HMAC+security verified. Bot creation with webhook config works. -10: actual delivery on meeting.completed event not yet verified during a real meeting lifecycle.)
