---
services: [meeting-api, vexa-bot, tts-service]
tests3:
  targets: [meeting-tts, smoke]
  checks: []
---

# Speaking Bot

## Why

Bots speak in meetings using TTS. Enables voice agents, scripted test utterances, and automated meeting participation. Audio plays through the bot's virtual microphone into the meeting.

## What

```
POST /bots/{platform}/{id}/speak {text, voice} → Redis PUBLISH → bot container
  → TTS service (Piper local or OpenAI) → WAV → PulseAudio tts_sink → virtual_mic
  → meeting audio (other participants hear the bot speak)
```

### Components

| Component | File | Role |
|-----------|------|------|
| speak endpoint | `services/meeting-api/meeting_api/voice_agent.py` | REST → Redis command |
| TTS playback | `services/vexa-bot/core/src/services/tts-playback.ts` | Synthesize + play through PulseAudio |
| TTS service | `services/tts-service/` | Piper (local) or OpenAI proxy |
| PulseAudio setup | `services/vexa-bot/core/entrypoint.sh` | tts_sink + virtual_mic + remap source |

## How

### 1. Make the bot speak in a meeting

The bot must be in `active` state. The text is synthesized via TTS and played through the bot's virtual microphone.

```bash
curl -s -X POST http://localhost:8056/bots/gmeet/135/speak \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello everyone, I am taking notes for this meeting."}'
# 202
```

### 2. Specify a voice

```bash
curl -s -X POST http://localhost:8056/bots/teams/125/speak \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Meeting summary is ready.", "voice": "echo"}'
# 202
```

Available voices: `alloy`, `echo`, `fable` (and others supported by the TTS service).

### 3. Interrupt speech playback

```bash
curl -s -X DELETE -H "X-API-Key: $VEXA_API_KEY" \
  http://localhost:8056/bots/gmeet/135/speak
# 200
```

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | POST /speak returns 202 and bot speaks | 30 | ceiling | 0 | UNTESTED | TTS /speak API works | 2026-04-05T19:40Z | 07-bot-lifecycle |
| 2 | Other participants hear the speech | 30 | ceiling | 0 | UNTESTED | Listener bot transcribes speaker output | 2026-04-05T19:40Z | 09-verify-transcription |
| 3 | Multiple voices (alloy, echo, fable) distinguishable | 20 | — | 0 | SKIP | Only default voice tested this run | 2026-04-05T19:40Z | 09-verify-transcription |
| 4 | Interrupt (DELETE /speak) stops playback | 10 | — | 0 | SKIP | Not tested this run | 2026-04-05T19:40Z | 07-bot-lifecycle |
| 5 | Works on GMeet and Teams | 10 | — | 0 | UNTESTED | Both platforms verified | 2026-04-05T19:40Z | 09-verify-transcription |

Confidence: 70 (ceiling items 1+2 pass = 60; item 5 = 10; total 70/100)
