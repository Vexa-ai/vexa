# Bot Overview

A **bot** is a short-lived worker that joins **one meeting** and streams audio for transcription (and optionally persists a recording).

## What You Get

Depending on your flags:

- `transcribe_enabled=true` → transcript `segments[]` (live via WebSocket and/or post-meeting via REST)
- `recording_enabled=true` → post-meeting `recordings[]` with playable/downloadable media

Recording persistence is separate from capture (Vexa must capture audio to transcribe).

See timing + alignment details in:

- [Core concepts](concepts.md)

## Supported Meeting Platforms

The API is platform-agnostic. The only differences are the meeting identifiers you pass.

| Platform | `platform` | `native_meeting_id` | `passcode` | Notes |
| --- | --- | --- | --- | --- |
| Google Meet | `google_meet` | `abc-defg-hij` | No | Often requires host admission (waiting room) |
| Microsoft Teams | `teams` | numeric ID (e.g. `1234567890123`) | Yes (`?p=` value) | `passcode` is required for most Teams links |
| Zoom | `zoom` | numeric ID (e.g. `12345678901`) | Optional (`?pwd=`) | Requires extra setup + usually Marketplace approval |

Meeting link extraction:

- [Meeting links & IDs](meeting-ids.md)

Zoom specifics:

- [Zoom limitations](platforms/zoom.md)
- [Zoom app setup](zoom-app-setup.md)

## Lifecycle (High-Level)

Typical flow:

1. You call `POST /bots`
2. Bot joins the meeting (`joining`, sometimes `awaiting_admission`)
3. Bot becomes `active`
4. You stop it (or the meeting ends)
5. Meeting becomes `completed` and post-meeting artifacts finalize (recordings + final transcript)

You can observe lifecycle changes via:

- Webhooks: [Webhooks](webhooks.md)
- WebSocket live transcript updates: [WebSocket guide](websocket.md)

## Live vs Post-Meeting

- **Live**: use WebSockets to stream segments with low latency
- **Post-meeting**: use `GET /transcripts/{platform}/{native_meeting_id}` for the finalized result
- **Playback** (when recording exists): stream bytes from `/recordings/.../raw` and highlight segments using `start_time`/`end_time`

References:

- [Transcripts API](api/transcripts.md)
- [Recordings API](api/recordings.md)

## Deleting / Anonymizing

`DELETE /meetings/{platform}/{native_meeting_id}` is designed to be safe to retry (idempotent) and scrubs meeting data for telemetry/usage tracking.

- [Meetings API](api/meetings.md)
- [Errors and retries](errors-and-retries.md)
