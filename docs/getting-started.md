# Self-Hosted Quickstart

Deploy Vexa yourself for full control over your data and infrastructure. This guide walks through the full self-hosted lifecycle.

> **Just want to try the API?** Use the [hosted service](quickstart.md) — no deployment needed.

---

## 1) Choose a Deployment

| Option | Best for | Guide |
|--------|----------|-------|
| **Vexa Lite** | Production — single container + external Postgres + remote transcription | [Deploy Vexa Lite](vexa-lite-deployment.md) |
| **Docker Compose** | Development/testing — full local stack | [Docker Compose setup](deployment.md) |

---

## 2) Create Users and API Tokens

Once your instance is running, use the Admin API to create users and mint API tokens.

```bash
export API_BASE="http://localhost:8056"
export ADMIN_TOKEN="your-admin-api-token"

# Create a user
curl -X POST "$API_BASE/admin/users" \
  -H "Content-Type: application/json" \
  -H "X-Admin-API-Key: $ADMIN_TOKEN" \
  -d '{"email": "user@example.com", "name": "User", "max_concurrent_bots": 2}'

# Generate an API token for the user (save it — cannot be retrieved later)
curl -X POST "$API_BASE/admin/users/1/tokens" \
  -H "X-Admin-API-Key: $ADMIN_TOKEN"
```

Full admin guide: [Admin API](self-hosted-management.md)

---

## 3) Send a Bot to a Meeting

```bash
export API_KEY="YOUR_USER_API_TOKEN"
```

### Google Meet

```bash
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij",
    "recording_enabled": true,
    "transcribe_enabled": true,
    "transcription_tier": "realtime"
  }'
```

### Microsoft Teams

Teams requires the numeric meeting ID (not the full URL). If your Teams URL contains `?p=...`, pass it as `passcode`.

```bash
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "platform": "teams",
    "native_meeting_id": "1234567890123",
    "passcode": "YOUR_TEAMS_P_VALUE",
    "recording_enabled": true,
    "transcribe_enabled": true,
    "transcription_tier": "realtime"
  }'
```

### Zoom

Zoom requires extra setup and (typically) Marketplace approval. See: [Zoom Integration Setup Guide](zoom-app-setup.md)

```bash
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "platform": "zoom",
    "native_meeting_id": "YOUR_MEETING_ID",
    "passcode": "YOUR_PWD",
    "recording_enabled": true,
    "transcribe_enabled": true,
    "transcription_tier": "realtime"
  }'
```

Full API details: [API overview](user_api_guide.md)

---

## 4) Watch Transcripts (REST + WebSocket)

### REST

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/transcripts/google_meet/abc-defg-hij"
```

### WebSocket (recommended for live)

Use the WebSocket guide for low-latency updates:

- [WebSocket guide](websocket.md)

---

## 5) Stop the Bot

```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/bots/google_meet/abc-defg-hij"
```

---

## 6) Post-Meeting: Recording & Playback

If recording is enabled and a recording was captured, `GET /transcripts/{platform}/{native_meeting_id}` includes a `recordings` array.

Playback/streaming options:

- `/recordings/{recording_id}/media/{media_file_id}/raw` (authenticated streaming; supports `Range`/`206` seeking)
- `/recordings/{recording_id}/media/{media_file_id}/download` (presigned URL for object storage backends)

Storage configuration and playback behavior: [Recording storage](recording-storage.md)

---

## 7) Cleanup: Delete/Anonymize a Meeting

```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/meetings/google_meet/abc-defg-hij"
```

This purges transcript artifacts and recording objects (best-effort) and anonymizes the meeting for telemetry.

---

## 8) Use the Dashboard (optional)

For a web UI to join meetings, view live transcripts, and review history, use the open-source Vexa Dashboard:

- [Dashboard](ui-dashboard.md)
