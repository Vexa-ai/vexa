# Voice Agent (Meeting Interaction)

The Voice Agent feature turns the Vexa bot from a passive observer into a fully interactive meeting participant. An external agent or application can control the bot via REST API to speak, read/write chat, and share visual content during a live meeting.

> **Status**: Available on branch `feature/interactive-voice-agent`. Requires `voice_agent_enabled: true` when requesting a bot.

## Overview

When Voice Agent is enabled, the bot gains these capabilities:

| Capability | Description | Status |
|------------|-------------|--------|
| **Speak** | Text-to-speech or raw audio playback into the meeting | Working |
| **Chat write** | Send messages to the meeting chat | Working |
| **Chat read** | Capture messages from the meeting chat | Working |
| **Screen share** | Display images, URLs, or video via screen share | Working |
| **Virtual camera** | Show avatar/content via the bot's camera feed | Experimental |

All capabilities are controlled via REST endpoints and the existing WebSocket event stream.

## Requesting a Voice-Agent-Enabled Bot

Add `voice_agent_enabled: true` to the standard `POST /bots` request:

```bash
curl -X POST https://api.example.com/bots \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -d '{
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij",
    "bot_name": "AI Assistant",
    "voice_agent_enabled": true
  }'
```

This changes the bot's audio pipeline: instead of feeding silence as mic input, the bot reads from a PulseAudio virtual microphone that receives TTS audio. The bot starts muted and auto-unmutes only when speaking.

## Prerequisites

- **OpenAI API key**: Set `OPENAI_API_KEY` in your environment (used for TTS synthesis). Passed through `docker-compose.yml` to the bot container.
- **PulseAudio**: Already configured in the bot container (`entrypoint.sh`). No manual setup needed.

---

## API Reference

All voice agent endpoints follow the pattern:

```
{METHOD} /bots/{platform}/{native_meeting_id}/{action}
```

Authentication: `X-API-Key` header (same as all Vexa endpoints).

---

### Speak (Text-to-Speech)

Make the bot speak in the meeting. The bot unmutes, plays the audio, then re-mutes.

#### Send text (bot synthesizes speech)

```
POST /bots/{platform}/{native_meeting_id}/speak
```

**Request body:**
```json
{
  "text": "Hello everyone, here is the summary.",
  "provider": "openai",
  "voice": "alloy"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | — | Text to speak (mutually exclusive with `audio_url`/`audio_base64`) |
| `provider` | string | `"openai"` | TTS provider (`openai`) |
| `voice` | string | `"alloy"` | Voice ID (OpenAI voices: `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`) |

#### Send pre-rendered audio

```json
{
  "audio_url": "https://example.com/greeting.wav",
  "format": "wav"
}
```

Or with base64-encoded audio:

```json
{
  "audio_base64": "UklGR...",
  "format": "wav"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `audio_url` | string | — | URL to audio file |
| `audio_base64` | string | — | Base64-encoded audio data |
| `format` | string | `"wav"` | Audio format: `wav`, `mp3`, `pcm`, `opus` |
| `sample_rate` | int | `24000` | Sample rate for PCM audio (Hz) |
| `channels` | int | `1` | Channel count for PCM audio |

#### Interrupt speech

```
DELETE /bots/{platform}/{native_meeting_id}/speak
```

Immediately stops any ongoing speech. The bot re-mutes.

#### Examples

```bash
# Text-to-speech
curl -X POST https://api.example.com/bots/google_meet/abc-defg-hij/speak \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -d '{"text": "Hello, I am the meeting assistant.", "voice": "nova"}'

# Interrupt
curl -X DELETE https://api.example.com/bots/google_meet/abc-defg-hij/speak \
  -H 'X-API-Key: YOUR_API_KEY'
```

---

### Chat

Read and write messages in the meeting chat.

#### Send a chat message

```
POST /bots/{platform}/{native_meeting_id}/chat
```

**Request body:**
```json
{
  "text": "Here is the meeting summary so far."
}
```

The bot opens the chat panel (if not already open), types the message, and sends it.

#### Read chat messages

```
GET /bots/{platform}/{native_meeting_id}/chat
```

**Response:**
```json
{
  "messages": [
    {
      "sender": "John Smith",
      "text": "Can you share the action items?",
      "timestamp": 1707933456.123,
      "isFromBot": false
    },
    {
      "sender": "AI Assistant",
      "text": "Here are the action items...",
      "timestamp": 1707933460.456,
      "isFromBot": true
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `sender` | string | Participant name (or bot name for bot messages) |
| `text` | string | Message content |
| `timestamp` | float | Unix timestamp (seconds) |
| `isFromBot` | bool | Whether the bot sent this message |

#### Examples

```bash
# Send chat message
curl -X POST https://api.example.com/bots/google_meet/abc-defg-hij/chat \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -d '{"text": "Meeting summary: 3 action items identified."}'

# Read chat messages
curl https://api.example.com/bots/google_meet/abc-defg-hij/chat \
  -H 'X-API-Key: YOUR_API_KEY'
```

---

### Screen Share

Display visual content (images, web pages, video) via screen sharing.

#### Show content

```
POST /bots/{platform}/{native_meeting_id}/screen
```

**Request body:**
```json
{
  "type": "image",
  "url": "https://example.com/chart.png",
  "start_share": true
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | — | Content type: `image`, `url`, `video` |
| `url` | string | — | Content URL |
| `start_share` | bool | `true` | Auto-start screen sharing (if not already sharing) |

Content types:
- **`image`**: Renders image fullscreen on black background
- **`url`**: Opens the URL in a browser window (e.g., Google Slides)
- **`video`**: Plays video fullscreen with autoplay

#### Stop screen share

```
DELETE /bots/{platform}/{native_meeting_id}/screen
```

Stops screen sharing and clears the display.

#### Examples

```bash
# Share an image
curl -X POST https://api.example.com/bots/google_meet/abc-defg-hij/screen \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -d '{"type": "image", "url": "https://example.com/quarterly-chart.png"}'

# Share a webpage (e.g., slides)
curl -X POST https://api.example.com/bots/google_meet/abc-defg-hij/screen \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -d '{"type": "url", "url": "https://docs.google.com/presentation/d/..."}'

# Stop sharing
curl -X DELETE https://api.example.com/bots/google_meet/abc-defg-hij/screen \
  -H 'X-API-Key: YOUR_API_KEY'
```

---

## WebSocket Events

When voice agent is enabled, the bot publishes additional events on the WebSocket connection and the Redis channel `va:meeting:{meeting_id}:events`:

| Event | Description |
|-------|-------------|
| `speak.started` | Bot started speaking (includes `text` if from TTS) |
| `speak.completed` | Speech playback finished |
| `speak.interrupted` | Speech was interrupted via DELETE |
| `chat.received` | New chat message captured (includes `sender`, `text`, `timestamp`) |
| `chat.sent` | Bot sent a chat message |
| `screen.sharing_started` | Screen sharing started (includes `content_type`) |
| `screen.sharing_stopped` | Screen sharing stopped |

---

## Architecture

```
External Agent
      │
      ▼
  API Gateway (:8056)
      │
      ▼
  Bot Manager (:8080)
      │
      ▼ Redis Pub/Sub
  bot_commands:meeting:{id}
      │
      ▼
  Bot Container
  ├── TTS Playback   → PulseAudio → meeting mic
  ├── Microphone      → DOM click (mute/unmute)
  ├── Chat Service    → DOM read/write
  ├── Screen Content  → Xvfb rendering
  └── Screen Share    → DOM click (present screen)
```

### Audio Pipeline (TTS → Meeting)

```
OpenAI TTS API
  → PCM Int16LE stream (24 kHz, mono)
  → paplay --raw --rate=24000 --device=tts_sink
  → PulseAudio tts_sink (null sink, 44.1 kHz)
  → tts_sink.monitor
  → virtual_mic (remap source)
  → Chromium default audio source
  → WebRTC → meeting participants hear speech
```

### Screen Content Pipeline

```
API request (image URL)
  → Playwright opens content page on Xvfb (:99, 1920x1080)
  → Content rendered fullscreen
  → Bot clicks "Present now" in Google Meet
  → --auto-select-desktop-capture-source selects Xvfb screen
  → Participants see shared screen with content
```

---

## Platform Support

| Feature | Google Meet | Teams | Zoom |
|---------|:----------:|:-----:|:----:|
| Speak (TTS) | ✅ | Planned | Planned |
| Chat write | ✅ | Planned | Planned (SDK) |
| Chat read | ✅ | Planned | Planned (SDK) |
| Screen share | ✅ | Planned | Planned (SDK) |
| Virtual camera | Experimental | — | — |

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes (for TTS) | OpenAI API key for text-to-speech |

### Bot Request Parameters

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `voice_agent_enabled` | bool | `false` | Enable voice agent capabilities |

---

## Known Limitations

1. **Virtual camera is experimental**: The canvas-based virtual camera (`replaceTrack` into WebRTC) works intermittently on Google Meet. Screen share is more reliable for displaying visual content.

2. **Single TTS provider**: Currently only OpenAI TTS is implemented. The architecture supports adding Cartesia (WebSocket, lowest latency) and ElevenLabs.

3. **Google Meet only**: Chat read/write and screen share are currently implemented for Google Meet. Teams and Zoom support is planned.

4. **No speech queue**: Rapid speak commands may overlap. The caller should wait for `speak.completed` before sending the next command, or use `DELETE /speak` to interrupt.

## Related

- [User API Guide](user_api_guide.md): Core API reference
- [WebSocket Guide](websocket.md): Real-time event streaming
- [Core Concepts](concepts.md): Meeting/bot/session model
- [GitHub Issue #120](https://github.com/Vexa-ai/vexa/issues/120): Detailed implementation status and learnings
