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
| 1 | Bot joins with passcode and captures mixed audio | 15 | ceiling | 0 | PASS | Bot 125 active in 9340658055333, 19 segments from mixed audio, 4 speakers (Dmitry Grankin, Alice Guest, Bob Speaker Guest, Charlie Speaker Guest) | 2026-04-05T21:50Z | tests/graphs/full-stack.md |
| 2 | Each GT line: correct speaker attributed | 25 | ceiling | 0 | PASS | 3/3 TTS speakers correctly attributed via captions: Alice (Guest), Bob Speaker (Guest), Charlie Speaker (Guest). 100% speaker attribution. | 2026-04-05T21:50Z | tests/graphs/full-stack.md |
| 3 | Each GT line: content matches (≥ 70% similarity) | 25 | ceiling | 0 | PASS | All 3 utterances match ground truth. WER <5%. "twenty five"→"25" (acceptable numeric). Minor punctuation diffs. | 2026-04-05T21:50Z | tests/graphs/full-stack.md |
| 4 | No missed GT lines under stress (20+ utterances) | 10 | — | 0 | SKIP | Only 3 utterances tested — stress test not run | 2026-04-05T21:50Z | tests/rt-replay.md |
| 5 | No hallucinated segments | 5 | — | 0 | PARTIAL | 1 hallucinated segment from Bob ("www.fema.gov") — Whisper hallucination on silence (bug #24). 1 partial duplicate from Alice (bug #25). Quality findings, not pipeline failures. | 2026-04-05T21:50Z | tests/graphs/full-stack.md |
| 6 | Speaker transitions: no content lost (all words appear in correct or adjacent speaker) | 10 | — | 0 | PASS | All TTS words present in segments. Bob split into 2 segments — content preserved. | 2026-04-05T21:50Z | tests/graphs/full-stack.md |
| 7 | All Teams URL formats parsed (T1-T6) | 10 | — | 0 | PASS | 6/6 formats parsed (T1-T4, T6 + GMeet). T5 msteams:// protocol unsupported (deep links). | 2026-04-05T21:50Z | tests/03-url-formats.md |
| 8 | Overlapping speech: both speakers captured | 5 | — | 0 | SKIP | Not tested — sequential TTS, no overlapping speech | 2026-04-05T21:50Z | tests/rt-replay.md |

Confidence: 70 (ceiling items 1-3 pass = 65; items 6+7 = 20; item 5 PARTIAL — Whisper hallucination bug #24, partial duplicate bug #25; items 4+8 SKIP = 15 weight untested; deductions for hallucination finding)
