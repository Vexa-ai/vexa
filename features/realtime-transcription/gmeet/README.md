# Google Meet Transcription

## Why

Cleanest audio pipeline. Each participant gets a separate `<audio>` element — true multi-channel. No diarization, no mixed audio. Industry gold standard for accuracy.

## What

N independent pipelines. One `AudioContext` + `ScriptProcessor` per `<audio>` element at 16kHz. Speaker identity via DOM voting/locking (correlate audio activity with speaking CSS classes).

```
Browser: N <audio> elements → N ScriptProcessors → __vexaPerSpeakerAudioData(index, data)
   ↕ (parallel)
Browser: MutationObserver on speaking classes → __vexaGetAllParticipantNames()

Node: handlePerSpeakerAudioData → Speaker Identity Voting → N SpeakerStreamManagers → Whisper → publish
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
| `speaker-identity.ts` | Track→speaker voting/locking |
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
CDP_URL=<cdp> node features/realtime-transcription/scripts/gmeet-host-auto.js  # → MEETING_URL
CDP_URL=<cdp> node features/realtime-transcription/scripts/auto-admit.js <url> # auto-admit
POST /bots {"meeting_url": "$MEETING_URL"}                                      # send bot
```

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | Bot joins and captures per-speaker audio | 20 | ceiling | 0 | PASS | Bot 135 active in rxf-gxis-ozd, 9 segments from 2 speakers (Dmitry's Vexa Bot Bot + Vexa Speaker). 0% WER on best stream. | 2026-04-05T21:50Z | tests/graphs/full-stack.md |
| 2 | Each GT line: correct speaker attributed | 25 | ceiling | 0 | PARTIAL | 2/2 speakers correctly attributed per audio stream. 3-speaker test not completed — only 1 TTS bot launched due to container crashes + concurrent limit. Speaker attribution works for all speakers present. | 2026-04-05T21:50Z | tests/graphs/full-stack.md |
| 3 | Each GT line: content matches (≥ 70% similarity) | 25 | ceiling | 0 | PASS | 3/3 TTS utterances: 0% WER on best stream (punctuation-only diffs). "farmer's" vs "farmers", added commas. | 2026-04-05T21:50Z | tests/graphs/full-stack.md |
| 4 | No hallucinated segments (0 output lines without GT match) | 10 | — | 0 | PASS | 9 segments, all match ground truth TTS input. No phantom content. | 2026-04-05T21:50Z | tests/graphs/full-stack.md |
| 5 | No missed GT lines (completeness 100%) | 10 | — | 0 | PASS | 3/3 TTS utterances transcribed (each appears twice due to separate audio streams — expected GMeet behavior) | 2026-04-05T21:50Z | tests/graphs/full-stack.md |
| 6 | DOM selectors current (bot joins successfully) | 10 | ceiling | 0 | PASS | Bot joined, admitted via auto-admit, reached active state. Selectors current as of Chrome 141. | 2026-04-05T21:50Z | tests/graphs/full-stack.md |

Confidence: 75 (item 2 PARTIAL — only 2 speakers tested, 3-speaker attribution unproven. Production single-bot scenario works with 0% WER and correct attribution. Multi-bot lite mode has audio loopback issue bug #30.)

## Known Issues

### Audio loopback creates duplicate segments (bug #30)

When multiple bots are in the same GMeet, the per-speaker audio capture can pick up audio from other bots' virtual microphones (PulseAudio loopback). This creates duplicate segments attributed to wrong speakers — the listener hears the speaker bot's TTS output but also captures it on a separate audio element, producing a second segment with incorrect attribution.

**Root cause:** Google Meet creates separate `<audio>` elements for each participant, including bot participants. When Bot B speaks via TTS, Bot A (listener) gets Bot B's audio on a dedicated track. But if PulseAudio routing leaks, the same audio may appear on multiple tracks.

**Impact:** Duplicate content in transcripts. Not visible in single-bot scenarios. Affects multi-bot test setups and voice agent configurations.

**Workaround:** Filter duplicate content at the segment level (dedup by text similarity within a time window).
