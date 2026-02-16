# Zoom

> Important: Zoom Marketplace approval can take weeks to months. Before approval, you should assume you can reliably join only meetings created/hosted by the authorizing account. The hosted Vexa Zoom app is not approved at the time of writing.

Zoom bots require additional configuration (Zoom OAuth + Meeting SDK + OBF token flow).

## Native meeting ID and passcode

For Zoom, you must pass the numeric meeting ID:

- URL: `https://us05web.zoom.us/j/12345678901?pwd=...`
- `native_meeting_id`: `12345678901`

If your URL includes `?pwd=...`, you may pass it as `passcode`:

```json
{
  "platform": "zoom",
  "native_meeting_id": "12345678901",
  "passcode": "YOUR_PWD"
}
```

## Setup guide

Use this guide for the exact OAuth + OBF flow and environment variables:

- [Zoom Integration Setup Guide](../zoom-app-setup.md)

## API example

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
