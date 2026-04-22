---
services:
- meeting-api
- vexa-bot
- tts-service
---

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 75**

# Google Meet Transcription

## Why

Cleanest audio pipeline. Each participant gets a separate `<audio>` element — true multi-channel. No diarization, no mixed audio. Industry gold standard for accuracy.

## What

N independent pipelines. One `AudioContext` + `ScriptProcessor` per `<audio>` element at 16kHz. Speaker identity via DOM voting/locking (correlate audio activity with speaking CSS classes).

```
Browser: N <audio> elements -> N ScriptProcessors -> __vexaPerSpeakerAudioData(index, data)
   ↕ (parallel)
Browser: MutationObserver on speaking classes -> __vexaGetAllParticipantNames()

Node: handlePerSpeakerAudioData -> Speaker Identity Voting -> N SpeakerStreamManagers -> Whisper -> publish
```

### Speaker identity

Track N has audio but we don't know WHO. DOM tiles show WHO is speaking but not WHICH track.

1. Audio arrives on track N
2. Query browser: who has speaking CSS class? (`Oaajhc`, `HX2H7`, `wEsLMd`, `OgVli`)
3. If exactly 1 speaker active: vote `track N = name`
4. After 2 votes at 70% ratio: lock permanently
5. Constraints: one-name-per-track, one-track-per-name

Primary protection: `isDuplicateSpeakerName` dedup check (first-assignment). Voting is backup.

### Key files

| File | Role |
|------|------|
| `index.ts` (`startPerSpeakerAudioCapture`) | Browser-side AudioContext/ScriptProcessor per element |
| `index.ts` (`handlePerSpeakerAudioData`) | Node-side: speaker resolution, VAD, buffer feed |
| `speaker-identity.ts` | Track->speaker voting/locking |
| `googlemeet/recording.ts` | Browser-side MutationObserver, participant counting |
| `googlemeet/selectors.ts` | All Google Meet DOM selectors (obfuscated, change with UI updates) |

### Key selectors

| Selector | Purpose |
|----------|---------|
| `[data-participant-id]` | Participant tile |
| `span.notranslate` | Participant name |
| `.Oaajhc` | Speaking animation |
| `.gjg47c` | Silence |
| `button[aria-label="Leave call"]` | Leave button |

## How

```bash
# Automated — no human needed
CDP_URL=<cdp> node features/realtime-transcription/scripts/gmeet-host-auto.js  # -> MEETING_URL
CDP_URL=<cdp> node features/realtime-transcription/scripts/auto-admit.js <url> # auto-admit
POST /bots {"meeting_url": "$MEETING_URL"}                                      # send bot
```

## DoD

Machine-readable definitions live in [`./dods.yaml`](./dods.yaml). Human-readable summary:

| # | Check | Weight | Baseline | Evidence source |
|---|-------|--------|----------|-----------------|
| 1 | Bot joins and captures per-speaker audio | 20 | PASS | `tests3/tests/meeting-tts.sh` phase 2-3 (bot admission + audio frames) |
| 2 | Each GT line: correct speaker attributed | 25 | PASS (1.0) | `tests3/testdata/gmeet-compose-260405/pipeline/score.json:speaker_accuracy` |
| 3 | Each GT line: content matches (≥ 70% similarity) | 25 | PASS (0.93) | `score.json:avg_similarity` |
| 4 | No hallucinated segments (rate ≤ 0.45) | 10 | PASS | `score.json:hallucinations / gt_count` |
| 5 | No missed GT lines (completeness 100%) | 10 | PASS (1.0) | `score.json:completeness` |
| 6 | DOM selectors current | 10 | PASS | Chrome 141 verified; `GMEET_URL_PARSED` contract check |

Gate: **confidence_min = 75** (pre-refactor value, restored 2026-04-22).

## Known Issues

### Audio loopback creates duplicate segments (bug #30)

When multiple bots are in the same GMeet, the per-speaker audio capture can pick up audio from other bots' virtual microphones (PulseAudio loopback). This creates duplicate segments attributed to wrong speakers — the listener hears the speaker bot's TTS output but also captures it on a separate audio element, producing a second segment with incorrect attribution.

**Root cause:** Google Meet creates separate `<audio>` elements for each participant, including bot participants. When Bot B speaks via TTS, Bot A (listener) gets Bot B's audio on a dedicated track. But if PulseAudio routing leaks, the same audio may appear on multiple tracks.

**Impact:** Duplicate content in transcripts. Not visible in single-bot scenarios. Affects multi-bot test setups and voice agent configurations.

**Workaround:** Filter duplicate content at the segment level (dedup by text similarity within a time window).

### Whisper hallucination on silence (bug #24)

The current Whisper config occasionally emits phantom segments on long silences (>5 s). DoD #4 uses a rate-based threshold (hallucination_rate ≤ 0.45) to accept this baseline; tightening to absolute-zero requires an upstream fix in the Whisper pipeline, tracked separately.

## Release history

- **260422-zoom-sdk Pack H** (2026-04-22) — DoD sidecar + README body restored
  from pre-refactor state at commit `6694502^`. The 2026-04-18 sidecar
  migration silently emptied this file when the script didn't find
  frontmatter-shape DoDs; restoration also set the parity bar for the
  new `zoom-sdk` feature (Pack E) to match gmeet's shape.
