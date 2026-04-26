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

Zoom Web borrows gmeet's audio plumbing but invents its own (more
fragile) speaker-binding layer because Zoom's SFU breaks gmeet's track-
stability assumption. The result is gmeet-grade transcription quality
with a Zoom-specific misattribution race that does not exist on either
gmeet or msteams.

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

## Audio acquisition (gmeet-style, two paths in parallel)

The bot opens **two** audio capture paths simultaneously:

1. **Browser per-stream** — exactly the gmeet shape:
   `services/audio.ts:60-100` finds DOM `<audio>` elements with
   `srcObject instanceof MediaStream`. Each is subscribed via
   `ctx.createMediaStreamSource(stream)` → `ScriptProcessor` →
   per-stream PCM at 16 kHz mono. Lands at
   `__vexaPerSpeakerAudioData(speakerIndex, audioArray)` →
   `SpeakerStreamManager`.
2. **PulseAudio mix** — `recording.ts:121-127` spawns
   `parecord --device=zoom_sink.monitor --rate=16000 --channels=1
   --format=s16le`. The captured mixed stream feeds the durable
   `RecordingService` (MinIO upload) but **NOT** the transcription
   pipeline.

The two paths are independent: per-stream feeds Whisper; PulseAudio
feeds the recording. They observe the same audio but at different
granularities.

### What we observed in a real multi-speaker meeting (10 speakers)

```
[Zoom Web] Audio verification: 3 elements with audio streams (5 total media elements)
  Element 0 <audio>: paused=false, tracks=1, states=[{"enabled":true,"muted":false,"readyState":"live"}]
  Element 1 <audio>: paused=false, tracks=1, states=[{"enabled":true,"muted":false,"readyState":"live"}]
  Element 2 <audio>: paused=false, tracks=1, states=[{"enabled":true,"muted":false,"readyState":"live"}]
[PerSpeaker] Browser-side audio capture started with 3 streams
```

Three audio elements stayed open the entire meeting — the count did
**not** grow with participant count. Zoom's SFU recycles ~3 track slots
across all active speakers; track identity is opaque and volatile.

## Speaker-name binding (the departure)

gmeet's deal with track identity: **track 0 is Alice forever**. After
voting, `track-0 → Alice` is locked in `speaker-identity.ts` and trusted
for the rest of the meeting.

Zoom's SFU does not honor that contract. Track 0 might be Alice this
second, Bob next, Carol after that. PR #181's response:

- **Bypass `speaker-identity.ts` entirely** for Zoom
  (`services/vexa-bot/core/src/index.ts:1460` — `if (platformKey ===
  'zoom')` skips the voting/locking layer).
- **Poll Zoom's active-speaker DOM badge every 250 ms**
  (`platforms/zoom/web/recording.ts:175-218`).
- On every audio chunk arriving from any of the 3 streams, call
  `speakerManager.updateSpeakerName(speakerId, domSpeaker)` to remap
  (`index.ts:1478`).

This is a **third pattern**, neither gmeet nor msteams:

- gmeet: stable track → name (vote-and-lock).
- msteams: no track at all → caption events drive everything.
- **Zoom Web: volatile tracks + DOM-polled active-speaker overlay.**

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

1. **Speaker misattribution race** — the DOM-polling overlay is reactive
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

3. **Audio-join button selector stale** — `button.join-audio-container__btn`
   click times out 8 times in a loop (5 s each = 40 s wasted) before the
   bot proceeds. Audio capture works anyway because Zoom Web auto-joins
   audio without the button. Either update the selector or drop the loop.
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

**Total Wave 2 estimate: ~2 days** (was 1.5-2 d in the original groom
estimate; bumped after the misattribution race surfaced as a real
production issue, not a theoretical one).

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

# Intentionally un-gated: legacy feature carries no machine-readable

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 90%**
# DoDs yet. Populate `dods:` before this feature's next release or
# its expected behavior changes.
gate:
  confidence_min: 0    # not enforced until dods: is populated
dods: []   # intentionally un-gated, reason: DoDs not yet authored
