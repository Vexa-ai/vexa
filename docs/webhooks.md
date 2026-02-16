# Webhooks

Vexa can POST events to your server so you can react to meeting lifecycle changes and recording completion.

## Configure

Use the REST API:

- `PUT /user/webhook` (see also: [User Settings API](api/settings.md))

```bash
curl -X PUT "$API_BASE/user/webhook" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "webhook_url": "https://your-service.com/vexa/webhook",
    "webhook_secret": "optional-shared-secret"
  }'
```

Notes:

- `webhook_url` must be publicly reachable. Private/internal URLs are rejected (SSRF protection).
- If you set `webhook_secret`, Vexa will include `Authorization: Bearer <secret>` on webhook requests.
- For local development, use a tunnel (ngrok/cloudflared): [Local webhook development](local-webhook-development.md)

## Delivery

- Webhooks are best-effort. Your endpoint should respond quickly (2xx) and do any heavy work asynchronously.
- Your endpoint should be idempotent (you may receive retries or repeated events).

## Event Types

### `meeting.status_change`

Sent on **any** meeting status transition (requested, joining, awaiting_admission, active, stopping, completed, failed).

Example payload:

```json
{
  "event_type": "meeting.status_change",
  "meeting": {
    "id": 16,
    "user_id": 1,
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij",
    "constructed_meeting_url": "https://meet.google.com/abc-defg-hij",
    "status": "completed",
    "bot_container_id": "3f2f...9b1",
    "start_time": "2026-02-13T20:10:12Z",
    "end_time": "2026-02-13T20:44:51Z",
    "data": {
      "completion_reason": "stopped"
    },
    "created_at": "2026-02-13T20:10:00Z",
    "updated_at": "2026-02-13T20:44:51Z"
  },
  "status_change": {
    "from": "stopping",
    "to": "completed",
    "reason": "self_initiated_leave",
    "timestamp": "2026-02-13T20:44:51.123Z",
    "transition_source": "bot_callback"
  }
}
```

### `recording.completed`

Sent when a recording is finalized (recording was enabled and capture/upload succeeded).

Example payload:

```json
{
  "event_type": "recording.completed",
  "recording": {
    "id": 906238426347,
    "meeting_id": 16,
    "user_id": 1,
    "session_uid": "d6e337d6-92cd-452f-b003-23c5498091ef",
    "source": "bot",
    "status": "completed",
    "created_at": "2026-02-13T20:10:20Z",
    "completed_at": "2026-02-13T20:44:55Z",
    "media_files": [
      {
        "id": 906238426348,
        "type": "audio",
        "format": "wav",
        "storage_backend": "s3",
        "file_size_bytes": 1234567,
        "duration_seconds": 2079.2,
        "metadata": {
          "sample_rate": 16000
        },
        "created_at": "2026-02-13T20:44:55Z"
      }
    ]
  }
}
```
