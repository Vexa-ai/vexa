# Bots API

Bots join meetings and stream audio for transcription (and optionally persist a recording).

## Meeting IDs by Platform

Extract the `native_meeting_id` (and `passcode` when required) from the meeting URL:

| Platform | URL example | `native_meeting_id` | `passcode` |
|----------|-------------|---------------------|------------|
| Google Meet | `https://meet.google.com/abc-defg-hij` | `abc-defg-hij` | — |
| Microsoft Teams | `https://teams.live.com/meet/1234567890123?p=XYZ` | `1234567890123` | `XYZ` (required) |
| Zoom | `https://us05web.zoom.us/j/12345678901?pwd=...` | `12345678901` | optional |

## POST /bots

Create a bot for a meeting.

Common request fields:

- `platform` (`google_meet` | `teams` | `zoom`)
- `native_meeting_id`
- `passcode` (Teams required; Zoom optional)
- `recording_enabled` (optional)
- `transcribe_enabled` (optional)
- `transcription_tier` (`realtime` | `deferred`, optional)
- `voice_agent_enabled` (optional) -- enables [Voice Agent](voice-agent.md) capabilities (speak, chat, screen share)

<Tabs>
  <Tab title="Google Meet">
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
  </Tab>

  <Tab title="Microsoft Teams">
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
  </Tab>

  <Tab title="Zoom">
> Caveat: until Marketplace approval, joining meetings outside the authorizing account may be limited.
>
> See [Zoom limitations](../platforms/zoom.md) and [Zoom app setup](../zoom-app-setup.md).

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
  </Tab>
</Tabs>

Returns the created meeting record.

<details>
  <summary><strong>Response (201)</strong></summary>

```json
{
  "id": 16,
  "user_id": 1,
  "platform": "google_meet",
  "native_meeting_id": "abc-defg-hij",
  "constructed_meeting_url": "https://meet.google.com/abc-defg-hij",
  "status": "requested",
  "bot_container_id": null,
  "start_time": null,
  "end_time": null,
  "data": {
    "passcode": null
  },
  "created_at": "2026-02-13T20:10:00Z",
  "updated_at": "2026-02-13T20:10:00Z"
}
```

</details>

## GET /bots/status

List bots currently running under your API key.

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/bots/status"
```

<details>
  <summary><strong>Response (200)</strong></summary>

```json
{
  "running_bots": [
    {
      "container_id": "3f2f...9b1",
      "container_name": "vexa-bot-google_meet-16",
      "platform": "google_meet",
      "native_meeting_id": "abc-defg-hij",
      "status": "Up 2 minutes",
      "normalized_status": "Up",
      "created_at": "2026-02-13T20:10:10Z",
      "labels": {
        "vexa.user_id": "1",
        "vexa.meeting_id": "16"
      },
      "meeting_id_from_name": "16"
    }
  ]
}
```

</details>

## PUT `/bots/{platform}/{native_meeting_id}/config`

Update an active bot configuration (currently supports `language` and `task`).

<Tabs>
  <Tab title="Google Meet">
```bash
curl -X PUT "$API_BASE/bots/google_meet/abc-defg-hij/config" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"language":"es"}'
```
  </Tab>

  <Tab title="Microsoft Teams">
```bash
curl -X PUT "$API_BASE/bots/teams/1234567890123/config" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"language":"es"}'
```
  </Tab>

  <Tab title="Zoom">
```bash
curl -X PUT "$API_BASE/bots/zoom/12345678901/config" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"language":"es"}'
```
  </Tab>
</Tabs>

<details>
  <summary><strong>Response (202)</strong></summary>

```json
{
  "message": "Reconfiguration request accepted and sent to the bot."
}
```

</details>

## DELETE `/bots/{platform}/{native_meeting_id}`

Stop a bot (remove it from the meeting).

<Tabs>
  <Tab title="Google Meet">
```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/bots/google_meet/abc-defg-hij"
```
  </Tab>

  <Tab title="Microsoft Teams">
```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/bots/teams/1234567890123"
```
  </Tab>

  <Tab title="Zoom">
```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/bots/zoom/12345678901"
```
  </Tab>
</Tabs>

<details>
  <summary><strong>Response (202)</strong></summary>

```json
{
  "message": "Stop request accepted and is being processed."
}
```

</details>
