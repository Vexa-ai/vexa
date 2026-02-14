# Quickstart (Hosted)

Get from **meeting link to transcript** in minutes using the Vexa hosted API. No infrastructure to set up.

## 1. Get your API key

Sign up and get your API key from the [Vexa Cloud dashboard](https://vexa.ai/dashboard/api-keys).

```bash
export API_BASE="https://api.cloud.vexa.ai"
export API_KEY="YOUR_API_KEY_HERE"
```

## 2. Send a bot to a meeting

### Google Meet

Extract the meeting code from the URL: `https://meet.google.com/abc-defg-hij` -> `abc-defg-hij`

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

Extract the numeric ID and passcode from the URL: `https://teams.live.com/meet/1234567890123?p=YOUR_PASSCODE`

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

> Zoom requires extra setup and typically Marketplace approval.
>
> See: [Zoom limitations](platforms/zoom.md) and [Zoom app setup](zoom-app-setup.md)

```bash
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "platform": "zoom",
    "native_meeting_id": "12345678901",
    "passcode": "OPTIONAL_PWD",
    "recording_enabled": true,
    "transcribe_enabled": true,
    "transcription_tier": "realtime"
  }'
```

Full reference: [Bots API](api/bots.md)

## 3. (Recommended) Configure a webhook for completion

Webhooks are the easiest way to know when post-meeting artifacts are ready.

```bash
curl -X PUT "$API_BASE/user/webhook" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "webhook_url": "https://your-service.com/vexa/webhook",
    "webhook_secret": "optional-shared-secret"
  }'
```

- Webhook guide: [Webhooks](webhooks.md)
- Local dev tunneling: [Local webhook development](local-webhook-development.md)

## 4. Fetch the transcript

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/transcripts/google_meet/abc-defg-hij"
```

The response contains:

- `segments[]`: transcript segments with `start_time`/`end_time`
- `recordings[]` (optional): recording + `media_files[]` for playback/download

Full reference: [Transcripts API](api/transcripts.md)

## Next Steps

- Live streaming: [WebSocket guide](websocket.md)
- Post-meeting recording & playback: [Recordings API](api/recordings.md) + [Recording storage](recording-storage.md)
- Delete/anonymize: [Meetings API](api/meetings.md) (and read: [Errors and retries](errors-and-retries.md))
- Self-hosted deployment: [Self-hosted quickstart](getting-started.md)
