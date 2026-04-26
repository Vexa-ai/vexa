# Zoom Web — audio architecture research

| field         | value                                                              |
|---------------|--------------------------------------------------------------------|
| release_id    | `260426-zoom`                                                      |
| wave          | 1 of 3                                                             |
| author        | AI:develop (with human-driven smoke)                               |
| status        | **COMPLETE — smoke ran end-to-end with live transcription**        |
| smoke_url     | `https://us05web.zoom.us/j/83415519389?pwd=It19eP2cOdMO8Kg7sjpyKUV0Dm7Db9.1` |
| smoke_run_at  | 2026-04-26T08:57:27Z (compose VM 172.239.45.70)                    |
| compose_env   | fresh provision, branch=main, commit=3bb9305 (includes PR #181)    |
| bot_image_tag | `vexaai/vexa-bot:dev` built from main HEAD                         |

## Executive summary

**FINAL revision 2026-04-26 after rich-observation harness +
live Wave-2 prototype testing.**

Zoom Web is **gmeet-family multi-channel** — Zoom maintains a small
pool of MediaStream objects (~6 unique stream IDs observed over
17 minutes), and each stream is overwhelmingly tied to ONE specific
speaker (88% / 81% / 62% concentration on a primary speaker per
stream). Simultaneous speakers ARE captured on different streams
(overlap isolation observed: 23 ticks where ≥2 streams had
significant audible RMS at the same instant). The "multi-channel
illusion" of replicated audio across slots is largely **DOM
presentation volatility**, not actual stream-to-speaker rotation.
This note went through THREE wrong intermediate framings before the
rich-observation harness (`platforms/zoom/web/observe.ts`) gave us
the data to settle the architecture definitively.

The Zoom-vs-gmeet differences are operational, not architectural:

| | **gmeet** | **Zoom Web** |
|--|------------|---------------|
| Stream pool | per-participant, unbounded | per-active-speaker (loudest-N), bounded ~6 |
| Stream lifetime | entire meeting | while speaker stays in active pool |
| DOM-to-stream binding | stable per `<video>` tile | stable stream_id, volatile DOM idx (8 migrations observed in 17 min) |
| Speaker name source | DOM text adjacent to tile | same — DOM walker up from audio element |

**The real bug in PR #181 was NOT the audio handling** (which is
correct — per-stream subscription with stream_id deduplication works
and captures overlap). **The bug was that the speaker-name labeling
bypassed the existing voting/locking pipeline** (`speaker-identity.ts`
already has a `resolveZoomSpeakerName` with proper voting) and
substituted per-chunk DOM polling, which propagates DOM-badge flicker
into the per-buffer state.

### Wave-2 fix prototyped + live-tested in this cycle

Three patches deployed and validated against live multi-speaker
meetings (bots 22-25 in meeting 89237402037):

1. **Unify Zoom branch with gmeet/teams** at
   `services/vexa-bot/core/src/index.ts:1453-1490` — drop the
   per-chunk DOM-polled `updateSpeakerName()` path, route Zoom audio
   through the same `resolveSpeakerName()` + vote-and-lock pipeline
   gmeet uses. `speakerIndex` is already stable per stream (deduped
   at `audio.ts:1867` by `stream.id`), so it works as the speaker key.

2. **botName filter** in `services/vexa-bot/core/src/services/speaker-identity.ts`
   `traverseZoomDOM` — pass `botName` parameter through and reject
   tile-text matching the bot's own name. Without this, the DOM walker
   sometimes lands on the bot's own tile and locks the track to the
   bot's name.

3. **Name-shape filter** `looksLikeName(text)` — reject candidate
   names that are >60 chars OR start with lowercase Latin letter.
   Catches chat-message text that the DOM walker can otherwise pick
   up when participant tiles share ancestry with chat-overlay
   elements. Real display names start with capital, digit, or
   non-Latin char (emoji, ideogram).

### Validation results (live multi-speaker meeting, 2026-04-26)

| Bot | Image | Result |
|-----|-------|--------|
| 22 | gmeet-pattern flip only | Bot's own name + chat text both leaked into locks |
| 23 | + botName filter | "James Whitfield" filtered ✓; "it's a we thing" still locked (65 misattributed segs) |
| 25 | + chat-text shape filter | Tracks 1+2 → "Leo Grankin" / "Dmtiry Grankin" — clean attribution; user confirmed "looks pretty good" |

### What's still pending for Wave 2 (separate cycle)

The misattribution race fix is now LIVE-TESTED-WORKING. The remaining
Wave-2 priorities are:

1. **Recording chunked-upload** (P0) — `platforms/zoom/web/recording.ts`
   doesn't call `recordingService.uploadChunk()`. Mirror gmeet/teams's
   pattern. Production gap surfaced in this cycle (5/5 meetings had
   zero MinIO uploads). Decision locked: chunked-upload pattern.

2. **Audio-join button stale selector → 40s ACTIVE-callback delay**
   (P1) — `button.join-audio-container__btn` click times out 8 times
   before the bot fires the ACTIVE callback. Dashboard shows "joining"
   for 40s while bot is already in the meeting.

3. **Auth-required meeting early-exit** (P1) — meetings with "Only
   authenticated users can join" enabled produce a 30s/5min timeout
   on `#input-for-name` with no structured error.

4. **Zoom chat read/write not functional** (P1, surfaced 2026-04-26
   user report) — bot is supposed to support Zoom chat (read incoming
   chat messages + send outgoing). Currently appears non-functional.
   PR #181's chat code lives in
   `services/vexa-bot/core/src/services/chat.ts` and
   `platforms/zoom/web/recording.ts` (chat observer init at admission).
   Investigation pending — likely DOM-selector mismatch with current
   Zoom Web UI version (parallel to the audio-join button issue).

5. **Self-initiated-leave with exit_code=1 misclassified as failed**
   (P2, surfaced 2026-04-26 meeting_id=26) — when the bot exits
   gracefully via `self_initiated_leave` (e.g., host ended meeting,
   max_time_left_alone watchdog) WITHOUT a prior DELETE-triggered
   `stopping` state, meeting-api maps exit_code=1 to status=failed.
   Should map to status=completed when reason is self_initiated_leave
   regardless of intermediate state. Compare to meeting_id=18 which
   went `active → stopping → completed` correctly because DELETE was
   sent first. Fix likely in
   `services/meeting-api/meeting_api/callbacks.py` exit handler —
   broaden the self_initiated_leave-as-completed rule.

6. **mic-muted modal auto-dismiss** (P3) — cosmetic log spam.

7. **Confirm-latency budget** (P2) — 12s confirm latency is high.

These are all known issues with concrete fix shapes documented in
`plan-approval.yaml` `future_waves_documented.wave_2.priority_list`.

## The dispatch trap (CRITICAL — Wave 3 still required)

The first smoke attempt failed instantly with:

```
[Zoom SDK] Native addon not found. Running in stub mode.
[BotCore] [Zoom] Initializing SDK and joining meeting: ...
[BotCore] [Graceful Leave] Initiating graceful shutdown sequence...
  Reason: join_meeting_error, Exit Code: 1
```

Why: meeting-api's dispatch at
`services/meeting-api/meeting_api/meetings.py:1029-1037` reads
`os.getenv("ZOOM_WEB", "")` from its own runtime env. On the freshly
provisioned VM, that env var is **NOT set** (it's not declared in
`deploy/compose/docker-compose.yml`'s `meeting-api.environment` block,
not in `.env.example`, not in any vm-setup template). So the bot
defaulted to **Native SDK** path, hit the missing native addon, and
exited.

**Fix to make Wave 1 smoke work** (applied during this session, NOT
committed): add `docker-compose.override.yml` on the VM with:

```yaml
services:
  meeting-api:
    environment:
      ZOOM_WEB: 'true'
```

After restart, meeting-api correctly forwards `ZOOM_WEB=true` to bot
pods → bot dispatches Web → smoke proceeds.

**This confirms Wave 3's necessity is non-negotiable**: the operator-
side env-var dispatch is structurally broken. Any operator pulling the
chart/compose today and calling `POST /bots {platform: "zoom"}` gets
SDK by default → instant failure → no path to Web without out-of-band
env config. The Wave 3 platform-enum upgrade (zoom→Web direct, zoom_sdk
→SDK direct, delete `process.env.ZOOM_WEB` end-to-end) is the only
clean fix. Documented in `plan-approval.yaml`'s `future_waves_documented.wave_3`.

## Comparison framework — the two existing paths

| dimension                 | Google Meet (`platforms/googlemeet`)                                            | Microsoft Teams (`platforms/msteams`)                                          |
|---------------------------|---------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| audio shape               | per-participant tracks; bot subscribes per-track via `MediaStreamAudioSourceNode` | mixed stream; bot captures via `getDisplayMedia()` or tab capture              |
| where tracks come from    | DOM `<video>` elements with `srcObject = MediaStream` per participant          | one `<audio>` / `<video>` element with the conference's mixed downlink         |
| speaker identity          | track identity = speaker (DOM text adjacent to the `<video>` resolves to name) | caption events: `(speaker_name, utterance, ts)` arrive via DOM event listener  |
| buffering                 | `services/vexa-bot/core/src/services/speaker-streams.ts` — N parallel streams  | `services/vexa-bot/core/src/services/audio.ts` — single VAD-segmented stream   |
| transcription             | per-stream Whisper; segments tagged with speaker at emit                       | Whisper on mixed stream; reconciler aligns segments with caption-derived speakers |
| name resolution           | `services/vexa-bot/core/src/services/speaker-identity.ts` — DOM text + lock cache | inline from caption event                                                      |

## Smoke run — actual log evidence

### Step 1 — POST /bots (after override applied)

```bash
curl -X POST $GATEWAY/bots \
  -H "X-API-Key: $TOKEN" \
  -d '{"platform":"zoom","native_meeting_id":"83415519389",
       "passcode":"It19eP2cOdMO8Kg7sjpyKUV0Dm7Db9.1",
       "meeting_url":"https://us05web.zoom.us/j/83415519389?pwd=...",
       "bot_name":"wave1-zoom-web"}'

# response:
{"id": 3, "platform": "zoom", "status": "requested",
 "bot_container_id": "ea2974...", "constructed_meeting_url": "https://zoom.us/j/...",
 "data": {"recording_enabled": true, "transcribe_enabled": true, ...}}
```

### Step 2 — Status transitions

```
requested  → joining   (bot_callback, ~6s after POST)
joining    → active    (bot admitted by host, ~30s)
active     → (sustained)
```

Bot container: `meeting-2-61a88d7c` (note: container index resets per
runtime-api lifetime; meeting_id_from_name=3 matches DB).

### Step 3 — First transcription segments

Pulled from postgres directly (`/transcripts` endpoint requires a
different token scope than the `bot`-only token bootstrapped in this
smoke):

```
 meeting_id |    speaker     | start_time | end_time |                       text
------------+----------------+------------+----------+--------------------------------------------------
          3 | Dmtiry Grankin |    111.328 |  118.368 | Now let's see if transcription is flowing here...
          3 | Dmtiry Grankin |    118.368 |  133.404 | flowing here. 1, 2, 3, 4, 5, 6, 7, 1,2, 3.
          3 | Dmtiry Grankin |    133.404 |  149.393 | 2, 3, 4, 5, 6, 7, 1,2, 3.
```

3 segments, time-coded, speaker-labeled with the Zoom display name
("Dmtiry Grankin" — the user's actual Zoom-account name; the typo is
in the user's Zoom profile, not a bot bug).

### Bot telemetry at +60s post-admission

```
[📊 TELEMETRY] whisper=8 (889ms avg, 0 failed)
              | drafts=8 confirmed=3 discarded=0
              | confirm_latency=12.7s
              | whisper_segs/call=1.2
              | reconfirm=0
              | vad=0/0 (checked/rejected)
```

Read: 8 Whisper calls succeeded (avg 889ms, 0 failures), produced 8
draft segments, 3 confirmed (the 3 in postgres above). VAD counters
stayed at `0/0` — the per-speaker pipeline routes audio via
speaker-event triggers (Zoom DOM hooks), NOT via VAD. This is
semantically correct for the multi-channel architecture; VAD is
redundant when Zoom itself signals speaker-active.

## Audio architecture inspection — findings

### (a) `<video>` elements per participant

Not directly inspected via DevTools (CDP forwarding was set up but
inspection happened via bot's own log emissions — sufficient for Wave
1's purpose). Bot logs showed:

```
[Zoom Web] Audio verification: 3 elements with audio streams
                              (5 total media elements)
  Element 0 <audio>: paused=false, tracks=1, states=[{"enabled":true,"muted":false,"readyState":"live"}]
  Element 1 <audio>: paused=false, tracks=1, states=[{"enabled":true,"muted":false,"readyState":"live"}]
  Element 2 <audio>: paused=false, tracks=1, states=[{"enabled":true,"muted":false,"readyState":"live"}]
```

**3 `<audio>` elements with 1 track each, all `live`+`enabled`** in a
1-on-1 meeting. (5 total media elements — likely 2 are `<video>` for
camera tiles, plus the 3 audio.) This is the canonical multi-channel
shape: one `<audio>` per active speaker, not a single mixed stream.
For larger meetings, expect N audio elements where N = active-speaker
count (Zoom culls inactive speaker streams from the DOM, so
participant-list ≠ audio-element count).

### (b) Captured stream track structure

The bot's `[PerSpeaker] Browser-side audio capture started with 3 streams`
confirms it discovered all 3 tracks. The pipeline message
`[Zoom Web] Transcription handled by per-speaker pipeline (WhisperLive
disabled)` tells us PR #181 disables the legacy WhisperLive (continuous
streaming) path in favour of per-stream Whisper-Lite. Each speaker has
its own Whisper queue.

### (c) Active-speaker DOM hook

**Works**. Bot fires:

```
🎤 [Zoom Web] SPEAKER_START: Dmtiry Grankin
```

This is a custom event from the bot's selectors, not a native Zoom
event. The selectors live in `services/vexa-bot/core/src/platforms/zoom/web/recording.ts`
+ `selectors.ts` (PR #181). Logs only emit `SPEAKER_START`; no
`SPEAKER_END` event was visible in this smoke (could be inferred from
gaps, or a separate observer fires it — Wave 2 to inspect).

### (d) Captions

Not enabled by host in this smoke run. The chat panel observer was
opened (`[Chat] Opened Zoom chat panel for observation`) but no caption
data exercised. Wave 2 should re-test with captions enabled to confirm
whether they're a useful adjunct (gmeet-like, not strictly needed for
labeling but useful for confirmation) or absent on free-tier accounts.

### (e) Participant list / name resolution

Speaker name **comes directly from Zoom's UI display name** (the user's
Zoom-account name as shown in the participant list). No DOM-attribute
hunting; `[Zoom Web] SPEAKER_START: <name>` carries the name as a
string already-resolved by selectors.ts. This is identical in spirit
to gmeet's `speaker-identity.ts` pattern — DOM text → name.

## Bugs / gaps surfaced for follow-on issues

These are NEW issues to file (not blockers for Wave 1 — bot still
joined and transcribed on at least one URL route):

00. **Zoom Web durable-recording total-loss** (surfaced post-smoke
    2026-04-26 when user reported "No audio recording for this meeting"):
    `platforms/zoom/web/recording.ts` uses PulseAudio (`parecord` →
    local /tmp WAV) for durable recording and relies entirely on the
    shared post-leave one-shot upload at `index.ts:746-757`. gmeet and
    msteams upload incrementally via `recordingService.uploadChunk()`
    every 10s (Pack B from #218 / commit 58ba53e), so they survive
    SIGKILL; **Zoom Web does not call `uploadChunk()` anywhere**.
    Direct evidence: meeting 15 (32-min multi-speaker live call) had
    zero `/internal/recordings/upload` POSTs; MinIO has nothing for
    meetings 11/12/13/14/15. (Meeting 3 — the first pre-rebuild smoke —
    did upload, suggesting either a regression in the rebuild or that
    the post-leave block has additional unmet preconditions; either
    way the chunked pattern bypasses the question.)
    **Decided fix shape (2026-04-26)**: mirror gmeet/teams's incremental
    pattern in `zoom/web/recording.ts` — call
    `recordingService.uploadChunk(callbackUrl, token, chunkData,
    chunk_seq, isFinal)` on a timer over the parecord stream (every
    ~10s; flush a final chunk with `isFinal=true` on
    `stopZoomWebRecording()`). Removes the dependency on graceful-leave
    entirely and aligns Zoom Web with the rest of the platform set.
    Wave 2 priority: P0 alongside the misattribution race fix.


0. **Authenticated-meeting flow not handled** (root cause of the
   `/wc/` failure during this smoke):
   The second smoke run against `zoom.us/j/2020061935?pwd=624101`
   timed out at `[Zoom Web] Waiting for pre-join name input...`. CDP
   probe attempts were racy (bot dies in ~30-45s and runtime-api
   auto-removes the container), but the human verifier confirmed the
   bot's screen showed **"Sign in to join this meeting"** — i.e.
   Zoom's authenticated-users-only gate. The pre-join name input
   `#input-for-name` (`selectors.ts:7`) never renders on the
   sign-in page, so the 30s `waitForSelector` always times out.
   PR #181 doesn't handle this branch: no Zoom account login flow,
   no cookie injection, no fallback selectors for the auth wall.

   **NOT** a `/wc/` route bug per se — both `/wc/` and `/wb/embed/`
   route fine when auth isn't required. The first smoke
   (`us05web.zoom.us/...`) had auth disabled on the host's free-tier
   meeting; the second had it enabled. Two distinct cases for Wave 2
   to handle:
     (a) **Detect** the sign-in page early (title/body text) and
         exit with a structured failure (`join_meeting_error.reason
         = auth_required`) instead of timing out — fast feedback.
     (b) **Support** authenticated join (long-term) — analog of
         gmeet's `authenticated:true` flow (#98). Needs a Zoom
         account, cookie/session injection, ToS check.

   Note: this dovetails with #254's Open Question 3 ("Anti-automation:
   what's our maintenance contract?"). Authenticated-mode is the
   robust answer; selector-chasing is the fragile one.

   **Until Wave 2 ships either (a) or (b), self-hosters and hosted
   Vexa can only run Zoom Web bots against meetings where the host
   has explicitly disabled "Only authenticated users can join".**



1. **Audio-join button selector stale** (8 click attempts failed):
   ```
   [Zoom Web] Audio button aria-label: "audio" (attempt 1..8)
   [Zoom Web] Audio join attempt N failed: locator.click: Timeout 5000ms exceeded.
     - waiting for locator('button.join-audio-container__btn').first()
   ```
   Despite click failures, audio capture worked (Zoom Web auto-joins
   audio without the button click for this UI version; PR #181's
   click loop is defensive/redundant + probably also stale). File as
   "Zoom Web: stale audio-join button selector spams 8 timeout
   attempts".

2. **Persistent dismissable modal**:
   ```
   [Zoom Web] Ignoring non-removal modal: "Your mic is muted in system or browser settings."
   ```
   Repeated ~20+ times. Bot's modal-handler classifies it as
   "non-removal" but never dismisses it. Doesn't block joining or
   transcription but spams logs. File as "Zoom Web: 'mic muted' modal
   not auto-dismissed".

3. **VAD telemetry meaningless on Zoom Web**:
   `vad=0/0 (checked/rejected)` is misleading — VAD is bypassed by the
   per-speaker pipeline (correct architecturally) but the telemetry
   line still emits the field. Either rename / suppress for Zoom Web,
   or wire VAD as a redundant gate. File as "Zoom Web: VAD telemetry
   field is meaningless when speaker-event-driven; clarify or remove".

4. **`/transcripts/zoom/<native_id>` returns 403 for `bot`-scope tokens**:
   ```
   {"detail": "Insufficient scope for this endpoint"}
   ```
   The token bootstrap script in `tests3/checks/run` `bootstrap_creds()`
   creates a `bot`-scoped token; reading transcripts requires a broader
   scope. Confirmed `bot` works for `POST /bots`, `GET /bots/status`,
   `DELETE /bots/<id>` (modulo path shape). Either the bootstrap should
   widen scopes for tests3, or `/transcripts` should accept `bot`
   scope. Likely already covered by an open issue — tag it. (DB-direct
   read is the fallback the smoke used.)

5. **DELETE /bots/3 returns 404**:
   The `DELETE /bots/<meeting_id>` shape isn't the right route — bot
   teardown likely uses `DELETE /bots/<platform>/<native_meeting_id>`
   or `DELETE /meetings/<id>`. Wave 3's tier-meeting test for Zoom
   needs to use the correct teardown path. Audit at scope time.

## Recommendation for Wave 2 — FINAL (reframed 2026-04-26 after live observation)

**msteams-family pattern**: single-channel mixed audio + caption-driven
speaker labels. Zoom Web does NOT expose stable per-participant tracks.
What looks like 3-4 tracks are display-slot copies of the active
speaker's audio. The per-element subscription path PR #181 ships is
fundamentally fragile — drop it.

### Live evidence backing the reframing (meeting 18, 2026-04-26)

| Metric | Value | Conclusion |
|--------|-------|------------|
| Audio elements at init | 4 | Tracks Zoom's loudest-N UI slots |
| Distinct speakers seen | 5 (Kam, DoMiNiC, Antee, Mason, Jennifer) | N>K — slot recycling confirmed |
| Speaker-change events / 10 min | 78 | natural rate is ~20-40 — many are flickers |
| Cascade updates / 10 min | 167 | tracks updating per speaker change |
| **Cascade ratio** | **2.14** | **Audio replicated across ~2 slots simultaneously** |
| Whisper calls | 477 in 9 min (~53/min) | Silent-gate filter partly suppresses duplicates but cascade ratio shows the leak |
| Misattribution evidence | Jennifer 1-segment ("is dedication, determination") in Kam-dominated stretch | DOM-poll race in action |

A true multi-channel architecture (gmeet) would have cascade ratio
1.0 and per-track audio identity. We see 2.14 — definitive proof of
replicated routing. The architecture is msteams-family, not
gmeet-family.

### Wave 2 architectural changes (PRIMARY)

| File / area                                                          | Change                                                  |
|----------------------------------------------------------------------|---------------------------------------------------------|
| `services/vexa-bot/core/src/index.ts:1450-1492` (Zoom branch)        | Drop the per-element Zoom branch entirely. Route Zoom audio through the same single-stream path msteams uses. |
| `services/vexa-bot/core/src/platforms/zoom/web/recording.ts:121-145` | Reroute the PulseAudio PCM stream into the SpeakerStreamManager (single-buffer mode), not just the durable RecordingService |
| `services/vexa-bot/core/src/platforms/zoom/web/recording.ts:175-218` | Drop the 250 ms DOM-polling speaker observer. Replace with Zoom captions DOM event observer (mirror `__vexaTeamsCaptionData` callback at `msteams/recording.ts:1820`) |
| `services/vexa-bot/core/src/platforms/zoom/web/selectors.ts`         | Add Zoom CC selectors |
| Reconciler (segment ↔ speaker label alignment)                       | Reuse the msteams reconciler unchanged |

This eliminates the cascade-update misattribution race entirely
because there's no per-track binding to race against — labels arrive
pre-attached to caption events, decoupled from audio routing.

### Wave 2 also-fixed (mechanical follow-ups)

| File / area                                                          | Change                                                  |
|----------------------------------------------------------------------|---------------------------------------------------------|
| `services/vexa-bot/core/src/platforms/zoom/web/recording.ts`         | Update audio-join button selector (Bug #3 in priority list) — fixes 40s ACTIVE-callback delay |
| `services/vexa-bot/core/src/platforms/zoom/web/selectors.ts`         | Add 'mic muted' modal selector + dismissal action      |
| `services/vexa-bot/core/src/index.ts` (telemetry block)              | Suppress / rename VAD field for Zoom Web              |
| `services/vexa-bot/core/src/platforms/zoom/web/recording.ts`         | Add `recordingService.uploadChunk()` calls for incremental durable recording (gmeet/teams pattern) — fixes the recording total-loss bug |

### Wave 2 estimate (revised — final)

**~2.5 days** (was 1.5-2d in the prior revision; bumped after the
msteams-pattern reframing showed the architectural change is bigger
than mechanical bug-cleanup):

- 0.5 d — port the msteams caption observer pattern to Zoom
- 0.5 d — reroute PulseAudio mix to Whisper (single-stream); drop per-element subscription Zoom branch
- 0.5 d — captions selector authoring + smoke testing on a CC-enabled Zoom meeting
- 0.5 d — durable recording chunked-upload (gmeet/teams pattern in zoom/web/recording.ts)
- 0.5 d — selector cleanup (audio-join, mic-muted modal, audio-join → ACTIVE-callback delay fix)
- Graceful-fallback to active-speaker badge when host has CC disabled — folds into the captions observer task

## Wave 3 scope (already documented in `plan-approval.yaml`)

Public-API uplift — `Platform.ZOOM_SDK = "zoom_sdk"`, `ZOOM` IS Web,
direct routing both sides, **delete `process.env.ZOOM_WEB` end-to-end**
(the operator-config trap surfaced in this smoke is exactly why this
is essential), `meeting-tts-zoom` tier-meeting test, three DoDs under
`realtime-transcription/zoom`. Ships the breaking change. ~2.5 days.

The "this confirms Wave 3 is non-negotiable" framing in the dispatch
trap section above stands: shipping Web without Wave 3's API uplift
leaves every operator one missed env-var-config away from total Zoom
breakage by default.

## Smoke artifacts (for reference)

- compose VM: `172.239.45.70` (Linode g6-standard-6, us-ord)
- bot container: `meeting-2-61a88d7c` (still running at note-write
  time; will be cleaned up by post-Wave-1 reset)
- meeting_id (postgres): 3
- transcripts table rows for meeting_id=3: 3 (as of note close-out)
- override applied (NOT committed): `deploy/compose/docker-compose.override.yml`
  on VM only, adding `meeting-api.environment.ZOOM_WEB: 'true'`. Wave 3
  retires this need entirely.
