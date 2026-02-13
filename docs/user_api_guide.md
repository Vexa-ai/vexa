# API Overview

The Vexa API is platform-agnostic: you use the same endpoints for Google Meet, Teams, and Zoom.

The only platform differences are in the **meeting identifiers** you pass (`native_meeting_id`, and `passcode` for Teams).

## Authentication

All requests use:

- `X-API-Key: <your token>`

Hosted users can get an API key from:

- https://vexa.ai/dashboard/api-keys

Self-hosted users can create users/tokens via:

- [`docs/self-hosted-management.md`](self-hosted-management.md)

## Base URL

Set these once in your shell:

```bash
export API_BASE="http://localhost:8056"   # or https://api.cloud.vexa.ai
export API_KEY="YOUR_API_KEY_HERE"
```

## The Happy Path (3 requests)

### 1) Send a bot to a meeting

- `POST /bots`

Examples (Meet/Teams/Zoom) are here:

- [`docs/api/bots.md`](api/bots.md)

### 2) Read the transcript

- `GET /transcripts/{platform}/{native_meeting_id}`

Examples are here:

- [`docs/api/transcripts.md`](api/transcripts.md)

### 3) Post-meeting playback (if a recording exists)

If recording was enabled and capture succeeded, the transcript response includes:

- `recordings[]` → each has `media_files[]`

For browser playback, stream through the API:

- `GET /recordings/{recording_id}/media/{media_file_id}/raw` (supports `Range`/`206` seeking)

Full recordings reference:

- [`docs/api/recordings.md`](api/recordings.md)

## Meeting Links & IDs (Important)

Before you integrate, skim this once:

- [`docs/meeting-ids.md`](meeting-ids.md)

## Reference

- Bots: [`docs/api/bots.md`](api/bots.md)
- Meetings: [`docs/api/meetings.md`](api/meetings.md)
- Transcripts: [`docs/api/transcripts.md`](api/transcripts.md)
- Recordings: [`docs/api/recordings.md`](api/recordings.md)
- User settings (recording defaults, webhooks): [`docs/api/settings.md`](api/settings.md)
- WebSocket (live streaming): [`docs/websocket.md`](websocket.md)

Zoom caveats:

- [`docs/platforms/zoom.md`](platforms/zoom.md)
- [`docs/zoom-app-setup.md`](zoom-app-setup.md)

