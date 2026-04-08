---
services: [meeting-api, vexa-bot, tts-service]
tests3:
  targets: [meeting-tts, contracts]
  checks: [TEAMS_URL_STANDARD, TEAMS_URL_SHORTLINK, TEAMS_URL_CHANNEL, TEAMS_URL_ENTERPRISE, TEAMS_URL_PERSONAL]
---

# MS Teams Transcription

## Why

Teams provides ONE mixed audio stream but gives live captions with perfect speaker attribution. Architecture: transcribe mixed stream with Whisper, label segments with caption speaker boundaries.

## What

1 pipeline on mixed stream. Audio queues in ring buffer. Captions decide who gets which audio.

```
Browser: 1 mixed <audio> → ScriptProcessor → Audio Queue (ring buffer, 3s)
   ↕ (parallel)
Browser: Caption Observer ([data-tid="author"]) → speaker change → flush queue to named speaker

Node: handleTeamsAudioData(speaker, data) → 1 SpeakerStreamManager → Whisper → speaker-mapper → publish
```

### Caption-driven speaker boundaries

Audio and captions travel separate paths with 1-2s latency gap. Ring buffer bridges this.

1. Audio arrives → queued in ring buffer (max 3s)
2. Caption observer detects speaker change → flush previous speaker's buffer
3. Caption text grows >3 chars → flush to current speaker
4. `speaker-mapper.ts` maps Whisper word timestamps to caption boundaries (most time overlap wins)

### Key files

| File | Role |
|------|------|
| `msteams/recording.ts` | Audio queue, silence filter, caption observer, routing |
| `msteams/captions.ts` | Enable live captions (guest + host paths) |
| `msteams/selectors.ts` | DOM selectors: `[data-tid="author"]`, `[data-tid="closed-caption-text"]` |
| `msteams/join.ts` | RTCPeerConnection hook, pre-join flow |
| `speaker-mapper.ts` | Word timestamp × caption boundary mapping |

### Differences from Google Meet

| Aspect | Google Meet | Teams |
|--------|-----------|-------|
| Audio | N per-speaker | 1 mixed |
| Speaker identity | DOM voting (inferred) | Caption author (explicit) |
| Overlapping speech | Natural separation | Both in same stream |
| VAD | Silero entry gate | Browser RMS filter |

## How

```bash
# Teams requires human-provided URL + passcode
POST /bots {
  "meeting_url": "https://teams.live.com/meet/...",
  "platform": "teams",
  "passcode": "..."   # required for anonymous join
}
```

Teams meetings require `passcode` field. Without it, bots can't pass lobby. API rejects unknown fields.

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | Bot joins with passcode and captures mixed audio | 15 | ceiling | 0 | PASS | Compose: meeting-tts-teams 3 segments, 3/4 phrases, 2 speakers. K8s: 22 segments, Russian speech. Lite: 8 segments with human speech. | 2026-04-09 | meeting-tts-teams (compose, helm, lite) |
| 2 | Each GT line: correct speaker attributed | 25 | ceiling | 0 | PASS | meeting-tts-teams: 2 speakers correctly attributed (Alice, Bob). K8s: Dmitry Grankin attributed. | 2026-04-09 | meeting-tts-teams |
| 3 | Each GT line: content matches (≥ 70% similarity) | 25 | ceiling | 0 | PASS | Compose: 3/4 phrases matched. K8s: Russian text correctly transcribed. | 2026-04-09 | meeting-tts-teams |
| 4 | No missed GT lines under stress (20+ utterances) | 10 | — | 0 | SKIP | Not tested — transcription-replay blocked by TTS throughput (B9) | 2026-04-09 | — |
| 5 | No hallucinated segments | 5 | — | 0 | PARTIAL | Whisper hallucination on silence (bug #24) still present. | 2026-04-09 | meeting-tts-teams |
| 6 | Speaker transitions: no content lost | 10 | — | 0 | PASS | Content preserved across speaker switches in all tests. | 2026-04-09 | meeting-tts-teams |
| 7 | All Teams URL formats parsed (T1-T6) | 10 | — | 0 | PASS | Smoke static checks pass on all deployments. | 2026-04-09 | smoke |
| 8 | Overlapping speech: both speakers captured | 5 | — | 0 | SKIP | Not tested | 2026-04-09 | — |

Confidence: 75 (ceiling items 1-3 PASS = 65; items 6+7 = 20; items 4+8 SKIP; item 5 PARTIAL. Tested on compose, helm, lite.)

## Known issue: captions activation fragile (B11)

Teams transcription depends on closed captions being enabled via CDP DOM click. Activation fails silently when DOM selectors change or dialog doesn't appear. Bot shows `whisper=0 vad=0/0` — no audio captured. Human must manually enable captions as workaround. See `features/realtime-transcription/README.md` F10 for details. Planned: switch to audio capture + whisper (same as GMeet approach).
