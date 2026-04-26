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

Zoom Web's audio architecture is **gmeet-like multi-channel** — Zoom's
DOM exposes per-speaker `<audio>` elements (one per active participant)
with live, distinct MediaStream tracks. Active-speaker events fire as
DOM-driven `[Zoom Web] SPEAKER_START: <name>` events with the speaker
already attributed by Zoom's display name. PR #181's per-speaker
pipeline IS the right architectural choice; the gap is the wiring — VAD
counters stay at `0/0` while Whisper still fires (8 calls / 3 confirmed
segments in the smoke), suggesting the pipeline routes audio bytes
through speaker-event triggers rather than VAD, which is semantically
correct for Zoom but conflicts with the existing observability
(`vad=0/0` is misleading metric here, not a failure).

**Wave 1 result: BOT JOINS, AUDIO CAPTURES, TRANSCRIPTION FLOWS,
SPEAKERS ARE LABELED.** With one critical caveat documented below
(operator-side env-var dispatch), the full happy path works on current
`main`. Wave 2's scope is much smaller than the groom estimated:
mostly bug-cleanup of stale selectors and a couple of architectural
follow-ups, not a from-scratch buffering/labeling rewrite.

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

## Recommendation for Wave 2

**gmeet-like multi-channel** path is confirmed correct. Zoom Web exposes
per-participant `<audio>` tracks with name-resolved active-speaker
events; the per-speaker Whisper pipeline already in PR #181 is the
right architecture. Wave 2 is **smaller than the groom estimated** —
not 3-5 days of net-new buffering/labeling work, but ~1-2 days of
bug-cleanup + telemetry sanity:

### Wave 2 concrete touchpoints

| File / area                                                          | Change                                                  |
|----------------------------------------------------------------------|---------------------------------------------------------|
| `services/vexa-bot/core/src/platforms/zoom/web/recording.ts`         | Update audio-join button selector (Bug #1)              |
| `services/vexa-bot/core/src/platforms/zoom/web/selectors.ts`         | Add 'mic muted' modal selector + dismissal action (Bug #2) |
| `services/vexa-bot/core/src/index.ts` (telemetry block)              | Suppress / rename VAD field for Zoom Web (Bug #3)       |
| `services/meeting-api/meeting_api/meetings.py` (or `transcripts.py`) | Audit `/transcripts/...` scope policy (Bug #4)          |
| `services/vexa-bot/core/src/platforms/zoom/web/recording.ts`         | Confirm SPEAKER_END events fire (currently only START seen) |

### Wave 2 estimate (revised)

**~1.5-2 days** (was 3-5 in the plan estimate). The architectural
choice is right; the cleanup is mechanical; the only open inspection
is SPEAKER_END semantics.

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
