---
services:
- meeting-api
- vexa-bot
---

# Real-time transcription — Zoom Web

Browser-automated Zoom path via Playwright (PR #181), running in the same
unified bot image as Google Meet and Teams. Native SDK track is a
separate subfeature scoped under #253 and out of this subfeature's scope.

## TL;DR

**Zoom Web is gmeet-family** (final classification after live
rich-observation harness + Wave-2 prototype testing 2026-04-26).
Each Zoom MediaStream is overwhelmingly tied to ONE specific speaker
(88%/81%/62% per-stream concentration measured live). Simultaneous
speakers go to different streams (overlap isolation works). Stream
pool is small (~6 unique IDs observed) and bounded by the
loudest-N display grid; gmeet's pool is unbounded by participant
count. Stream IDs are stable per speaker; what's volatile is the
DOM positions the streams get rendered through (8 migrations
observed in 17 min).

**The misattribution race users observed wasn't an architecture
problem** — the audio handling has been correct all along. The bug
was that PR #181 bypassed the existing `resolveZoomSpeakerName`
voting/locking pipeline in `speaker-identity.ts` and substituted
per-chunk DOM polling, which propagates DOM-badge flicker. Fix:
re-route Zoom audio through the same `resolveSpeakerName()` pipeline
gmeet/teams use. Live-tested in Wave 1 against multi-speaker
meetings; user-confirmed "looks pretty good".

See parent [`../README.md`](../README.md) for the cross-platform
comparison table and shared pipeline.

## Code home

| Path                                                                | Role |
|---------------------------------------------------------------------|------|
| `services/vexa-bot/core/src/platforms/zoom/web/index.ts`            | platform handler entry (`handleZoomWeb`) |
| `services/vexa-bot/core/src/platforms/zoom/web/join.ts`             | URL transform (`zoom.us/j/...` → `app.zoom.us/wc/.../join`), pre-join name fill, audio/video toggles, click Join |
| `services/vexa-bot/core/src/platforms/zoom/web/admission.ts`        | post-Join admission detection (waiting-room → Leave-button visible) |
| `services/vexa-bot/core/src/platforms/zoom/web/prepare.ts`          | post-admission setup (audio button, popup dismissal) |
| `services/vexa-bot/core/src/platforms/zoom/web/recording.ts`        | PulseAudio capture for durable recording + DOM-polled active-speaker → SPEAKER_START / SPEAKER_END events |
| `services/vexa-bot/core/src/platforms/zoom/web/selectors.ts`        | DOM selectors (name input, leave button, speaker tiles, captions, chat) |
| `services/vexa-bot/core/src/platforms/zoom/web/removal.ts`          | end-of-meeting / removed-by-host detection |
| `services/vexa-bot/core/src/platforms/zoom/web/leave.ts`            | graceful leave |

## Audio acquisition (current — to be redesigned in Wave 2)

The bot currently opens **two** audio capture paths simultaneously:

1. **Browser per-element** (PR #181's design — to be retired in Wave 2):
   `services/audio.ts:60-100` finds DOM `<audio>` elements with
   `srcObject instanceof MediaStream`. Each is subscribed via
   `ctx.createMediaStreamSource(stream)` → `ScriptProcessor` →
   per-stream PCM at 16 kHz mono. Lands at
   `__vexaPerSpeakerAudioData(speakerIndex, audioArray)` →
   `SpeakerStreamManager`. **The fundamental flaw**: these elements
   are **display slots**, not per-speaker channels. Zoom routes the
   active speaker's audio to multiple slots simultaneously; the bot
   processes duplicate audio (cascade ratio 2.14 observed in live
   meeting 18) and uses DOM polling to bind a name to each chunk.
   The DOM-poll lookup races with Zoom's mixer state, producing
   misattribution. Wave 2 retires this path in favor of caption-
   driven labeling on top of the PulseAudio mix.
2. **PulseAudio mix** (becomes the sole audio source in Wave 2):
   `recording.ts:121-127` spawns
   `parecord --device=zoom_sink.monitor --rate=16000 --channels=1
   --format=s16le`. The captured mixed stream is the canonical
   single-channel conference audio. Wave 2 will:
   (a) drop the per-element pipeline,
   (b) feed the PulseAudio mix into Whisper directly (msteams-style),
   (c) wire a Zoom captions observer (DOM event listener on Zoom's CC
   stream) for speaker labels.

### What we observed in real multi-speaker meetings

Meeting 15 (10 speakers, 32 min):
```
[Zoom Web] Audio verification: 3 elements with audio streams
[PerSpeaker] Browser-side audio capture started with 3 streams
```

Meeting 18 (5 speakers, 12+ min ongoing):
```
[Zoom Web] Audio verification: 4 elements with audio streams
[PerSpeaker] Browser-side audio capture started with 4 streams
```

The element count varies by meeting but **does not scale with
participant count** — it tracks Zoom's UI loudest-N slot affordance.
The audio routed to each slot is whichever speaker's audio Zoom
multiplexes into that visual position; the same audio replicates
across multiple slots (cascade ratio 2.14 measured on meeting 18 —
see Live observation evidence below).

## Speaker-name binding — current implementation (PR #181)

PR #181 attempts to demux Zoom's display layer back into per-speaker
streams via DOM polling:

- **Bypass `speaker-identity.ts` entirely** for Zoom
  (`services/vexa-bot/core/src/index.ts:1460` — `if (platformKey ===
  'zoom')` skips the voting/locking layer).
- **Poll Zoom's active-speaker DOM badge every 250 ms**
  (`platforms/zoom/web/recording.ts:175-218`).
- On every audio chunk arriving from any of the N streams, call
  `speakerManager.updateSpeakerName(speakerId, domSpeaker)` to remap
  (`index.ts:1478`).

This approach is **structurally fragile** because the slots are not
per-speaker channels — they're display affordances over a single
multiplexed audio stream. Live evidence:

| Test                              | Expected (true multi-channel) | Observed (Zoom Web) |
|-----------------------------------|-------------------------------|---------------------|
| Cascade ratio (tracks updated per speaker change) | 1.0   | **2.14**            |
| Tracks active at any moment       | 1 per speaker                 | 2-3 simultaneously  |
| Per-track audio identity          | Stable (Alice = track 0)      | None — slot is whichever speaker is loudest |

The 2.14 cascade ratio is **proof** that Zoom replicates the active
speaker's audio across multiple slots. In a true multi-channel
architecture, only ONE track would update on a speaker change.

## Wave 2 — switch to msteams-pattern

Zoom Web is fundamentally an **msteams-family platform**: single-channel
mixed audio + caption-driven speaker labels. Wave 2 will:

1. **Drop the per-element subscription** — retire the
   `__vexaPerSpeakerAudioData` path for Zoom.
2. **Feed PulseAudio mix to Whisper** — single audio stream input,
   matching msteams.
3. **Wire a captions observer** — Zoom Web supports closed captions;
   add a DOM event listener that captures `(speaker_name, utterance,
   timestamp)` tuples (analog of msteams's `__vexaTeamsCaptionData`
   callback).
4. **Reuse the msteams reconciler** — Whisper segments + caption-derived
   speaker labels merge identically to msteams.

This eliminates the cascade-update misattribution race entirely
because there's no per-track binding to race against — labels arrive
pre-attached to the caption events, decoupled from audio routing.

Trade-off: depends on host enabling closed captions. When CC is off,
the bot has only the active-speaker DOM badge as a fallback (the
current method's accuracy ceiling).

## Production observations (2026-04-26 smoke)

10 distinct speakers captured through 3 SFU audio tracks via per-chunk
name remap, ~21 minutes of meeting time, ~33 minutes of cumulative
attributed speech.

### Speaker leaderboard (cumulative, top 10)

```
Marion 7-3-23❤️           77 segs / 888 s
Mila C. 1/4/2015 Oakland  75 segs / 937 s
DoMiNiC ☀️ Light On...    27 segs / 370 s
Jennifer                  23 segs / 337 s
Victor Luv-❤️-            21 segs / 271 s
Tonya 10.30.25 (She/her)  17 segs / 197 s
Kam                       14 segs / 165 s
Rick C San Francisco       4 segs /  55 s
17574069537                4 segs /  46 s    (phone caller — numeric ID)
TRACEY M 10/27/2005 NWK    2 segs /  51 s
```

UTF-8 emoji + non-ASCII speaker names round-trip correctly through
postgres. Phone callers get their dial-in number as the speaker name.

### Pipeline telemetry

| Metric              | Value (after ~21 min)               |
|---------------------|-------------------------------------|
| Whisper calls       | 955                                 |
| Failed Whisper calls| 0 (100% backend reliability)        |
| Avg Whisper RT      | 696 ms                              |
| Drafts emitted      | 770                                 |
| Confirmed segments  | 272 (~35% confirmation rate)        |
| Confirm latency     | 12.4 s (speech → DB-stored segment) |
| VAD checks          | 0/0 (bypassed; speaker-event-driven)|

### Transcription quality (eyeroll on real conversation)

Conversational AAVE preserved intact, no over-correction, no
hallucinations or stuck-loops, profanity not over-filtered. Casing is
sometimes dropped (`"i know me i know mila..."`) — inherent to live
ASR on conversational speech, not a bug.

```
t=1236  Mila    : "Doesn't it cost to play pool?"
t=1247  Marion  : "he told my he ain't making no money there you are you get paid for us to be there"
```

## Known issues / Wave 2 priorities

Ranked by user-visible impact.

1. **Audio recording total-loss on Zoom Web** — `platforms/zoom/web/
   recording.ts` writes the durable WAV via PulseAudio (`parecord` →
   `/tmp/recording_<id>_<session>.wav`) and **does not call
   `uploadChunk()` anywhere**. Upload only fires from the shared
   post-leave block in `index.ts:746-757`. gmeet and msteams don't
   depend on that block — they upload incrementally via
   `recordingService.uploadChunk(...)` from MediaRecorder
   `ondataavailable` every 10 s (Pack B from #218 / commit `58ba53e`),
   so their durable recording survives SIGKILL.
   Production observation 2026-04-26: meeting 15 (32-min real
   multi-speaker call) produced ZERO uploads despite `recording_enabled=true`;
   only meeting 3 (the very first smoke on the original pre-rebuild
   bot image) has anything in MinIO.
   **Fix (chosen 2026-04-26): mirror gmeet/teams's incremental pattern
   for Zoom Web**: call `recordingService.uploadChunk(callbackUrl,
   token, chunkData, seq, isFinal)` from `appendPCMBuffer()` (or a
   timer over the parecord stream) every ~10 s. Removes the dependency
   on graceful-leave entirely; survives SIGKILL like the post-#218
   gmeet/teams pattern. **Estimate: half a day.**

2. **Speaker misattribution race** — the DOM-polling overlay is reactive
   only; no state lock. When speaker A addresses speaker B mid-utterance,
   Zoom briefly flips the active-speaker badge to B (B's tile lights up
   as they prepare to respond), the 250 ms poll catches B's name, the
   audio chunk lands in B's buffer, and A's words get attributed to B.
   Concrete production sample: at `t=1193.0` Marion's verbatim phrase
   "we don't they sell food we buy as nasty ass food" was attributed to
   DoMiNiC; the same phrase reappears correctly attributed to Marion at
   `t=1213.5`.
   **Fix shape**: sample DOM speaker name **on every audio chunk** (~256 ms
   chunk cadence anyway), not on a separate 250 ms timer. Inline lookup
   eliminates the lag window. **Estimate: half a day.**

2. **Auth-required meeting detection** — meetings with "Only
   authenticated users can join" enabled show a Zoom sign-in page where
   the bot's `#input-for-name` selector never renders. Bot times out at
   the wait. Symptom observed in production smoke against
   `zoom.us/j/2020061935?pwd=624101`. Need structured early-exit
   (`join_meeting_error.reason = auth_required`) instead of timeout.
   **Estimate: half a day.**

3. **Audio-join button selector stale → 40 s ACTIVE-callback delay**
   (UPGRADED P3 → P1 after meeting 18 production observation):
   `button.join-audio-container__btn` click times out 8 times in a
   loop (5 s each = ~40 s wasted). The retry loop runs **before** the
   bot fires the ACTIVE callback, so meeting-api / dashboard show
   "joining" for ~40 s while the bot is already in the meeting.
   Audio capture works anyway because Zoom Web auto-joins audio on
   the current UI version. Either update the selector or drop the
   loop entirely. Concrete trace from meeting 18:
   ```
   10:34:52 JOINING callback fired
   10:34:52 [Zoom Web] Bot immediately admitted — Leave button visible
   10:34:52 [Zoom Web] Audio join attempt 3 failed: Timeout 5000ms
            (attempts 4, 5, 6, 7, 8 — 30s burned)
   10:35:33 🔥 UNIFIED CALLBACK: ACTIVE                ← +41s wasted
   ```
   **Estimate: 1-2 hours.**

4. **`mic muted` modal not auto-dismissed** — Zoom shows a
   "Your mic is muted in system or browser settings." modal that the
   bot's modal-handler classifies as `non-removal` and ignores. Repeated
   ~20+ times per minute in logs (cosmetic, not functional). Add a
   targeted dismiss action.
   **Estimate: 1-2 hours.**

5. **Confirm-latency budget** — 12-13 s from speech to DB segment is high
   for live UX. Investigate whether reducing the Whisper resubmission
   window or confirm-threshold can drop it without hurting accuracy.
   **Estimate: half a day.**

6. **VAD telemetry field meaningless** — `vad=0/0` always (correct: VAD
   is bypassed by speaker-event triggers). The telemetry line still
   emits the field, misleading anyone reading it.
   **Estimate: 30 minutes.**

7. **SPEAKER_END events** — both START and END events fire in the live
   meeting (verified). No action needed; monitor in case behavior
   regresses.

**Total Wave 2 estimate: ~2.5 days** (was 1.5-2 d in the original groom
estimate; bumped after the misattribution race surfaced as a real
production issue, plus the Zoom-side recording-total-loss bug surfaced
during the live multi-speaker smoke).

## Known unknown — auth wall workarounds

Long-term: support authenticated join (analog of gmeet's
`authenticated:true` flow, issue #98). Needs a Zoom account, cookie /
session injection, ToS check. Not Wave 2 scope; tracked in #254 Open
Question 3.

Until either (a) early-exit detection or (b) authenticated join ships,
**Zoom Web bots only work against meetings without "Only authenticated
users can join" enabled.**

## Open questions for Wave 3 (out of this subfeature's scope)

Public-API uplift — `Platform.ZOOM` becomes the canonical Web platform
value; `Platform.ZOOM_SDK` opts into Native; operator-side `ZOOM_WEB=true`
env-var dispatch is retired end-to-end. Detailed in
`tests3/releases/260426-zoom/plan-approval.yaml` `future_waves_documented.wave_3`.

# DoDs

Authored 2026-04-26 (Wave 1 — release 260426-zoom). Each DoD anchors
to evidence from the live multi-speaker production smoke
(meeting_id=15) documented in
`tests3/releases/260426-zoom/audio-research.md` and the cross-platform
table in `../README.md`. Wave 3 will replace the research-note grep
checks with tier-meeting-test bindings (automated Zoom + TTS
ground-truth scoring, mirroring `meeting-tts-teams`).

**Verified (Wave 1 evidence-passing):**

| id                                          | weight | evidence                                              |
|---------------------------------------------|-------:|-------------------------------------------------------|
| bot-joins-zoom-web-on-real-meeting          |   10   | `ZOOM_WEB_LIVE_SMOKE_RECORDED` (compose)              |
| transcribes-zoom-web-end-to-end             |   15   | `ZOOM_WEB_LIVE_SMOKE_RECORDED` (compose)              |
| multi-speaker-scales-via-track-recycle      |   15   | `ZOOM_WEB_MULTI_SPEAKER_VERIFIED` (compose)           |
| audio-architecture-comparison-documented    |    5   | `ZOOM_WEB_AUDIO_ARCHITECTURE_DOCUMENTED` (compose)    |

**Known-gap (red until Wave 2 ships fixes):**

| id                                          | weight | evidence                                              |
|---------------------------------------------|-------:|-------------------------------------------------------|
| durable-recording-uploaded-to-minio         |   10   | `ZOOM_WEB_RECORDING_UPLOAD_FOLLOWUP_DOCUMENTED` (red) |
| speaker-attribution-no-polling-lag-race     |    5   | `ZOOM_WEB_RECORDING_UPLOAD_FOLLOWUP_DOCUMENTED` (red) |

Total weight verified: **45**. Total weight known-gap: **15**.
Confidence at Wave 1 close: **75%** (45 / 60). Wave 2 closes the
known-gap DoDs to bring confidence to 100%.

**DoDs:** evidence checks live in
`tests3/releases/260426-zoom/plan-approval.yaml`
`registry_changes_approved` · Gate: **confidence ≥ 75%**

gate:
  confidence_min: 75   # raised from 0 in Wave 1 — feature now gated
