# Transcripts API

Transcripts are available during and after a meeting.

For live meetings, prefer WebSockets:

- [`docs/websocket.md`](../websocket.md)

## GET /transcripts/{platform}/{native_meeting_id}

Fetch transcript segments (and meeting metadata) for a meeting.

Notes:

- If you update meeting metadata via `PATCH /meetings/{platform}/{native_meeting_id}`, the transcript response also includes `notes` (from `meeting.data.notes`).
- If recording was enabled and captured, the response includes `recordings` (used for post-meeting playback).

<Tabs>
  <Tab title="Google Meet">
```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/transcripts/google_meet/abc-defg-hij"
```
  </Tab>

  <Tab title="Microsoft Teams">
```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/transcripts/teams/9321836506982"
```
  </Tab>

  <Tab title="Zoom">
```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/transcripts/zoom/89055866087"
```
  </Tab>
</Tabs>

## POST /transcripts/{platform}/{native_meeting_id}/share

Create a public share link for a meeting transcript.

<Tabs>
  <Tab title="Google Meet">
```bash
curl -X POST \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/transcripts/google_meet/abc-defg-hij/share"
```
  </Tab>

  <Tab title="Microsoft Teams">
```bash
curl -X POST \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/transcripts/teams/9321836506982/share"
```
  </Tab>

  <Tab title="Zoom">
```bash
curl -X POST \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/transcripts/zoom/89055866087/share"
```
  </Tab>
</Tabs>

