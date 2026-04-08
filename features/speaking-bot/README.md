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
| 1 | POST /speak returns 202 and bot speaks | 30 | ceiling | 0 | PASS | **All deployments**: API returns 202, TTS synthesizes OK (200), human confirmed audio plays with short phrases. Untested with longer phrases — 7 code bugs found (concurrency, auto-mute race, unhandled promise) that may cause intermittent failures under load. Needs `transcription-replay` with longer phrases to fully validate. | 2026-04-08 | Phase 5a (helm), 5b (lite), tts-reliability (compose) |
| 2 | Other participants hear the speech | 30 | ceiling | 0 | PASS | Human confirmed TTS audio heard on all deployments with short phrases. Longer phrases + rapid switching untested. | 2026-04-08 | Phase 5a (helm), 5b (lite), tts-reliability (compose) |
| 3 | Multiple voices (alloy, echo, fable) distinguishable | 20 | — | 0 | SKIP | Only alloy + echo tested | 2026-04-08 | — |
| 4 | Interrupt (DELETE /speak) stops playback | 10 | — | 0 | SKIP | Not tested | 2026-04-08 | — |
| 5 | Works on GMeet and Teams | 10 | — | 0 | PASS | TTS sent and heard on both platforms. | 2026-04-08 | Phase 5a, 5b |

Confidence: 50 (TTS plays audio — human confirmed on all deployments. But 0/10 in tts-reliability test due to short phrases ("Test one") not producing transcribable segments. Transcription pipeline itself broken on compose — 0 segments even with human speech. Need to separate TTS reliability from transcription reliability.)

### tts-reliability test result (2026-04-08, compose/Teams)
- 10/10 speak commands accepted (HTTP 202)
- TTS service synthesized all 10 (200 OK)
- Human confirmed TTS audio heard in meeting
- 0/10 phrases found in transcript — but phrases too short for whisper ("Test one", "Test two")
- **Test design issue**: need longer phrases for reliable transcription
- **Separate issue**: compose transcription broken — 0 segments even for human speech (meeting 9842)

## TTS bugs found (2026-04-08)

### Audio delivery path
```
POST /speak → gateway → meeting-api (voice_agent.py:29-71)
  → Redis PubSub "bot_commands:meeting:{id}" (voice_agent.py:69-70)
  → Bot handleRedisMessage (index.ts:2028) → handleSpeakCommand (index.ts:898-926)
  → MicrophoneService.unmute() (DOM click)
  → ttsPlaybackService.synthesizeAndPlay()
    → POST TTS_SERVICE_URL/v1/audio/speech → 200 + PCM
    → unmuteTtsAudio() (pactl set-sink-mute tts_sink 0)
    → paplay --device=tts_sink (streams PCM)
    → muteTtsAudio() (pactl set-sink-mute tts_sink 1)
  → scheduleAutoMute(2000ms) for in-meeting mic
```

Two-level unmute required: PulseAudio sink + in-meeting mic DOM button.

### Bug 1 (HIGH): `playFromUrl()` never unmutes PulseAudio
**File**: `services/vexa-bot/core/src/services/tts-playback.ts:230-281`
`playFromUrl()` does NOT call `unmuteTtsAudio()` before playback or `muteTtsAudio()` on exit. `playPCM()` (line 67) and `playFile()` (line 168) both do. Any speak via `audio_url` plays into a muted sink → silence.

### Bug 2 (HIGH): Lite entrypoint does not mute tts_sink at startup
**File**: `deploy/lite/entrypoint.sh:289-304`
Lite's `setup-pulseaudio-sinks.sh` creates sinks but does NOT mute them. Per-bot entrypoint (`vexa-bot/core/entrypoint.sh:35-36`) does: `pactl set-sink-mute tts_sink 1`. In lite mode, virtual_mic starts hot → WebRTC picks up noise → platform VAD thinks someone is speaking. The unmute/mute cycle loses track of actual state.

### Bug 3 (MEDIUM): Redis subscribe drops unhandled Promise rejections
**File**: `services/vexa-bot/core/src/index.ts:2028-2031`
`handleRedisMessage(message, channel, page)` returns a Promise that is never awaited or caught. If `handleSpeakCommand` throws (paplay not found, PulseAudio error), rejection is silently swallowed. No error log, no recovery. 202 already sent.

### Bug 4 (MEDIUM): No concurrency guard on speak commands
**File**: `services/vexa-bot/core/src/index.ts:554-557, 898-926`
No check of `ttsPlaybackService.isPlaying()` before starting new speak. Rapid speak commands overwrite `paplayProcess` reference (line 381), orphaning first process. First process exit handler calls `muteTtsAudio()` while second is still playing → silence mid-playback.

### Bug 5 (MEDIUM): Auto-mute race with new speak commands
**File**: `services/vexa-bot/core/src/services/microphone.ts:26-27, 90-97`
`scheduleAutoMute(2000)` fires 2s after speech. If new speak arrives during window: unmute clears timer, but if mute's `page.evaluate` is already in-flight, DOM mute click arrives AFTER unmute → mic muted during playback.

### Bug 6 (LOW): Teams mic toggle missing early return
**File**: `services/vexa-bot/core/src/services/microphone.ts:216`
`toggleTeamsMic()` falls through to `return true` without checking if state already matches. Functionally correct but only checks first selector.

### Bug 7 (LOW): Shared PulseAudio in lite for concurrent bots
Per-bot sink isolation (`bot_sink_${meetingId}`) only created for Zoom Web (index.ts:2046-2060). Multiple bots in lite share `tts_sink`. One bot's `muteTtsAudio()` silences all others.

### Fixes needed (priority order)
1. `tts-playback.ts:playFromUrl()` — add `unmuteTtsAudio()`/`muteTtsAudio()` matching other methods
2. `deploy/lite/entrypoint.sh` — add `pactl set-sink-mute tts_sink 1` + `pactl set-source-mute virtual_mic 1` after sink creation
3. `index.ts:2030` — add `.catch()` on `handleRedisMessage` Promise
4. `index.ts:handleSpeakCommand` — check `isPlaying()`, queue or reject concurrent requests
5. `microphone.ts` — guard auto-mute: abort if another unmute happened since timer scheduled
