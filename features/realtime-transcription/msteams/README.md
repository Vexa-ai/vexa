---
services:
- meeting-api
- vexa-bot
- tts-service
---

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 75**

# MS Teams Transcription

## Why

Teams provides ONE mixed audio stream but gives live captions with perfect speaker attribution. Architecture: transcribe mixed stream with Whisper, label segments with caption speaker boundaries.

## What

1 pipeline on mixed stream. Audio queues in ring buffer. Captions decide who gets which audio.

```
Browser: 1 mixed <audio> -> ScriptProcessor -> Audio Queue (ring buffer, 3s)
   ↕ (parallel)
Browser: Caption Observer ([data-tid="author"]) -> speaker change -> flush queue to named speaker

Node: handleTeamsAudioData(speaker, data) -> 1 SpeakerStreamManager -> Whisper -> speaker-mapper -> publish
```

### Caption-driven speaker boundaries

Audio and captions travel separate paths with 1-2s latency gap. Ring buffer bridges this.

1. Audio arrives -> queued in ring buffer (max 3s)
2. Caption observer detects speaker change -> flush previous speaker's buffer
3. Caption text grows >3 chars -> flush to current speaker
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

Machine-readable definitions live in [`./dods.yaml`](./dods.yaml). Human-readable summary:

| # | Check | Weight | Baseline | Notes |
|---|-------|--------|----------|-------|
| 1 | Bot joins with passcode + captures mixed audio | 15 | PASS | compose/helm/lite verified 2026-04-09 |
| 2 | Each GT line: correct speaker attributed | 25 | PASS (0.8) | caption-author driven |
| 3 | Each GT line: content matches (≥70% similarity) | 25 | PASS (0.93) | Whisper pipeline |
| 4 | No missed GT lines under stress (20+ utterances) | 10 | SKIP | blocked by bug #B9 (TTS throughput) |
| 5 | No hallucinated segments (rate ≤ 2.0) | 5 | PARTIAL | bug #24 Whisper-on-silence |
| 6 | Speaker transitions: no content lost | 10 | PASS | ring-buffer flush on caption change |
| 7 | All Teams URL formats parsed (T1-T6) | 10 | PASS | TEAMS_URL_* contract checks |
| 8 | Overlapping speech: both speakers captured | 0 | SKIP | architecture-bound on caption-driven attribution |

Gate: **confidence_min = 75** (pre-refactor value, restored 2026-04-22). SKIP items don't count toward the score; PARTIAL items count with their weight (and get the baseline value).

## Known Issues

### Captions activation fragile (B11)

Teams transcription depends on closed captions being enabled via CDP DOM click. Activation fails silently when DOM selectors change or dialog doesn't appear. Bot shows `whisper=0 vad=0/0` — no audio captured. Human must manually enable captions as workaround. See `features/realtime-transcription/README.md` F10 for details. Planned: switch to audio capture + whisper (same as GMeet approach).

### Whisper hallucination on silence (bug #24)

DoD #5 uses a rate-based threshold (≤ 2.0 hallucinations per GT line) rather than absolute zero. The current Whisper config emits phantom segments on long silences, amplified on Teams by the mixed-stream architecture. Tightening requires an upstream Whisper fix; tracked separately.

### Live fires carried into future cycles

- **#171** — Teams consumer-URL (`teams.live.com/meet/<numeric>`) bots exit with admission_false_positive + exit 137 ~13s post-"admission". Deferred from release 260421.
- **#226** — Teams bot stuck on `light-meetings/launch` "Continue without audio or video?" modal; never clicks Join. Different URL shape from #171 but same family (consumer / anonymous join).

Both affect DoD #1 against specific URL shapes. Next msteams-focused cycle reconciles the Teams admission layer.

## Release history

- **260422-zoom-sdk Pack I** (2026-04-22) — DoD sidecar + README body restored
  from pre-refactor state at commit `6694502^`. Same migration oversight
  that hit gmeet (Pack H). Plan-stage reconciliation approved:
  - Item 4 stays SKIP pending bug #B9.
  - Item 5 threshold set to hallucination_rate ≤ 2.0 (matches baseline).
  - Item 8 weight 5 → 0 so totals sum to exactly 100.
