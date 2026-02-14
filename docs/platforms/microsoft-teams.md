# Microsoft Teams

This page covers Teams specifics: how to extract the meeting ID and passcode, and common join issues.

## Native meeting ID and passcode

For Teams, you must pass:

- `native_meeting_id`: the numeric meeting ID
- `passcode`: the value of the `?p=` query parameter from the URL

Example:

- URL: `https://teams.live.com/meet/1234567890123?p=YOUR_PASSCODE`
- `native_meeting_id`: `1234567890123`
- `passcode`: `YOUR_PASSCODE`

## API example

```bash
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "platform": "teams",
    "native_meeting_id": "1234567890123",
    "passcode": "YOUR_PASSCODE",
    "recording_enabled": true,
    "transcribe_enabled": true,
    "transcription_tier": "realtime"
  }'
```

Full API guide:

- [API overview](../user_api_guide.md)

## Common issues

### Bot does not join

Most often:

- wrong `native_meeting_id` (must be numeric only)
- missing/wrong `passcode` (the `p=` value)

### Bot joins but you see no transcript

Common causes:

- `transcribe_enabled=false`
- meeting audio not available to capture

See:

- [Troubleshooting](../troubleshooting.md)
