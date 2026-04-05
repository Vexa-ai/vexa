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
| 1 | POST_MEETING_HOOKS configured and fires | 25 | ceiling | 0 | SKIP | POST_MEETING_HOOKS not set in container — internal hooks disabled. Envelope code verified via import. | 2026-04-05T21:33Z | 13-webhooks |
| 2 | Webhook envelope has correct shape (event_id, api_version, data) | 25 | ceiling | 0 | PASS | build_envelope() verified: event_id=evt_..., api_version=2026-03-01, required keys present | 2026-04-05T21:33Z | 13-webhooks |
| 3 | HMAC signing works when secret provided | 20 | — | 0 | PASS | build_headers(secret) → X-Webhook-Signature: sha256=..., X-Webhook-Timestamp present | 2026-04-05T21:33Z | 13-webhooks |
| 4 | Delivery logged (success or failure) | 15 | — | 0 | PASS | 7 webhook log entries found, retry worker running | 2026-04-05T21:33Z | 13-webhooks |
| 5 | No internal fields leaked in payload | 15 | — | 0 | PASS | clean_meeting_data strips webhook_secret, webhook_delivery, webhook_deliveries, webhook_events | 2026-04-05T21:33Z | 13-webhooks |

Confidence: 75 (items 2-5 pass = 75; item 1 SKIP — POST_MEETING_HOOKS not configured so actual delivery never triggered. Envelope+HMAC+security verified via code import but not end-to-end.)
