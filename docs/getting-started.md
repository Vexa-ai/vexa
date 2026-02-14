# Getting Started (End-to-End)

This guide walks through the full Vexa lifecycle:

1. First steps (pick deployment)
2. Deploy
3. Manage users/tokens
4. Send bots to meetings
5. Retrieve transcripts (REST + WebSocket)
6. Post-meeting (recordings + playback)
7. Cleanup (delete/anonymize)
8. Use a UI (Vexa Dashboard)

If you already have a deployment, jump to **Send bots**.

<Card title="Prefer the shortest path?" icon="rocket" href="/quickstart">
  Follow the Quickstart (send a bot → fetch transcript → optional playback).
</Card>

---

## 1) Pick a Deployment Path

Choose one:

- **Hosted (fastest):** use the hosted API and dashboard.
- **Self-hosted Lite (recommended for production):** single container + external Postgres + external transcription.
- **Docker Compose (dev):** full local stack for development/testing.

Links:

- Hosted: https://vexa.ai
- Lite: [Vexa Lite deployment](vexa-lite-deployment.md)
- Docker Compose dev: [Docker Compose (dev)](deployment.md)

---

## 2) Get an API Key / Token

### Hosted

Get an API key from:

- https://vexa.ai/dashboard/api-keys

### Self-hosted

Create a user and API token with the admin API:

- [`docs/self-hosted-management.md`](self-hosted-management.md)

---

## 3) Send a Bot to a Meeting (API)

Set your base URL:

```bash
export API_BASE="http://localhost:8056" # or https://api.cloud.vexa.ai
export API_KEY="YOUR_API_KEY_HERE"
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
    "native_meeting_id": "9387167464734",
    "passcode": "YOUR_TEAMS_P_VALUE",
    "recording_enabled": true,
    "transcribe_enabled": true,
    "transcription_tier": "realtime"
  }'
```

### Zoom

Zoom requires extra setup and (typically) Marketplace approval to join meetings outside the authorizing account.

See:

- [`docs/zoom-app-setup.md`](zoom-app-setup.md)

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

Full API details:

- [`docs/user_api_guide.md`](user_api_guide.md)

---

## 4) Watch Transcripts (REST + WebSocket)

### REST

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/transcripts/google_meet/abc-defg-hij"
```

### WebSocket (recommended for live)

Use the WebSocket guide for low-latency updates:

- [`docs/websocket.md`](websocket.md)

---

## 5) Stop the Bot

```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/bots/google_meet/abc-defg-hij"
```

---

## 6) Post-Meeting: Recordings + Playback

If recording is enabled and a recording was captured, `GET /transcripts/{platform}/{native_meeting_id}` includes a `recordings` array.

Playback/streaming options:

- `/recordings/{recording_id}/media/{media_file_id}/raw` (authenticated streaming; supports `Range`/`206` seeking)
- `/recordings/{recording_id}/media/{media_file_id}/download` (presigned URL for object storage backends)

Storage configuration and playback behavior:

- [`docs/recording-storage.md`](recording-storage.md)

---

## 7) Cleanup: Delete/Anonymize a Meeting

```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/meetings/google_meet/abc-defg-hij"
```

This purges transcript artifacts and recording objects (best-effort) and anonymizes the meeting for telemetry.

See caveats in:

- [`docs/user_api_guide.md`](user_api_guide.md)

---

## 8) Use a UI (Vexa Dashboard)

For a web UI to join meetings, view live transcripts, and review history (including post-meeting playback when recordings exist), use Vexa Dashboard:

- [`docs/ui-dashboard.md`](ui-dashboard.md)
