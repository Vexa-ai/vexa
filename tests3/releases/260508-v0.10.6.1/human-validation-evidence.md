# Human Validation Evidence - 260508-v0.10.6.1

Collected: 2026-05-12T21:59:44Z

This file is evidence only. It does not flip any `approved: false` fields in
`human-checklist.yaml`; those remain for the human signer.

## Current Deployment

- Release tag: `0.10.6-260512-2336`
- Compose state tag: `0.10.6-260512-2336`
- Lite state tag: `0.10.6-260512-2336`
- Compose dashboard: `http://127.0.0.1:3001/login` returned HTTP 200
- Lite dashboard: `http://127.0.0.1:3100/login` returned HTTP 200
- Compose gateway docs: `http://127.0.0.1:8056/docs` returned HTTP 200
- Admin API docs: `http://127.0.0.1:8057/docs` returned HTTP 200

Running images observed:

- `vexa-lite` -> `vexa-lite:0.10.6-260512-2336` healthy
- `vexa-dashboard-1` -> `vexaai/dashboard:0.10.6-260512-2336`
- `vexa-api-gateway-1` -> `vexaai/api-gateway:0.10.6-260512-2336` healthy
- `vexa-meeting-api-1` -> `vexaai/meeting-api:0.10.6-260512-2336` healthy
- `vexa-runtime-api-1` -> `vexaai/runtime-api:0.10.6-260512-2336` healthy
- `vexa-admin-api-1` -> `vexaai/admin-api:0.10.6-260512-2336` healthy
- `vexa-tts-service-1` -> `vexaai/tts-service:0.10.6-260512-2336`
- `vexa-mcp-1` -> `vexaai/mcp:0.10.6-260512-2336`

## Machine-Verified Checklist Evidence

- `always-lite-no-errors`: `docker logs vexa-lite --tail=200 | grep -i error | tail -5` produced no lines.
- `always-lite-mem`: `docker stats --no-stream vexa-lite` reported `894.4MiB`, below 2 GiB.
- `always-compose-no-errors`: `docker compose -f deploy/compose/docker-compose.yml logs --tail=50 | grep -i error` produced no lines.
- `release-integrity-image-tag`: running compose/lite service tags match `deploy/compose/.last-tag`.
- `release-integrity-no-test-containers`: no `lifecycle-`, `webhook-test`, or `spoof-test` containers found.
- `release-integrity-ssot-env`: `.env` has zero missing keys versus `deploy/env-example`; required local overrides are non-empty.
- `tier2-vexa-lite-apple-silicon-docs`: `docs/vexa-lite-deployment.mdx` contains the Apple Silicon/Rosetta/#321 note.

## Focused Release Checks

- `POST_MEETING_HOOKS_FIRE_ONCE_PER_SESSION`: passed via `bash tests3/tests/v0.10.6.1-post-meeting-idempotency.sh fires_once_per_session`.
- `BOT_CREATE_HONORS_DISPATCH_CHECK_DENY`: passed via `STATE=$PWD/tests3/.state-compose bash tests3/tests/v0.10.6.1-dispatch-check-deny.sh bots_endpoint_honors_deny`.
- `TTS_AUTO_LANG_PICKS_RIGHT_VOICE`: passed via `TTS_URL=http://127.0.0.1:8002 bash tests3/tests/v0.10.6.1-tts-auto-lang.sh detects_and_picks_voice`; English, Spanish, Russian, and Japanese all returned HTTP 200 with non-empty WAV output.

## Prior Matrix Evidence

- Compose registry matrix: `STATE=$PWD/tests3/.state-compose tests3/lib/run-matrix.sh compose` passed.
- Lite registry matrix: `STATE=$PWD/tests3/.state-lite tests3/lib/run-matrix.sh lite` passed.
- Docker registry manifests verified for `vexaai/{api-gateway,admin-api,runtime-api,meeting-api,mcp,dashboard,tts-service,vexa-bot,vexa-lite}:0.10.6-260512-2336`.

## Still Requires Human Eyeballs / Real Meeting Conditions

- Magic-link login in the browser and visual `/meetings` render for compose/lite.
- Real Google Meet create -> active -> transcript -> delete -> completed -> transcript persistence.
- Multi-chunk dashboard playback and scrubber behavior.
- Browser-session delete before active.
- Finalizer overlap/race walkthrough.
- Voice-agent camera initialization with `voice_agent_enabled=true`.
- Teams "Continue without AV" modal handling.
- GMeet host-not-started fast-fail behavior.
- BYO TTS `/speak` playback into a real meeting.
- Stale audit decisions review.
- Any helm-only validation items remain outside the current local compose/lite evidence.

## Fresh Compose + Lite Human-Gate Bot Evidence — 2026-05-13T21:36Z

Single human admission/speech window on Google Meet:

Historical note: the compose transcript proof below was later superseded for
delivery handoff by meeting `10099`, recorded in the 2026-05-14T08:05Z
checkpoint.

- Meet URL: `https://meet.google.com/zdd-uytw-kec`
- Compose bot: `Vexa human gate 260514`
- Lite bot: `Vexa human gate lite 260514`

Machine-verified exact artifacts:

- Compose `meeting_id=10098`: `LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT` passed with `5` recording chunks and `16` transcript segments.
- Lite `meeting_id=181`: `LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT` passed with `2` recording chunks and `12` transcript segments.

Commands run:

```bash
STATE=tests3/.state-compose LIVE_BOT_MEETING_ID=10098 bash tests3/tests/live-bot-transcript-pipeline.sh
STATE=tests3/.state-lite LIVE_BOT_MEETING_ID=181 bash tests3/tests/live-bot-transcript-pipeline.sh
```

Cleanup:

- Compose stop accepted; `/bots/status` returned no running compose bots.
- Lite stop accepted; meeting `181` reached `completed` with `completion_reason=stopped`. No separate `meeting-181` Docker container remained visible from the host.

Operator correction captured:

- The Vexa dashboard operator skill now requires compose+lite bots to be dispatched in parallel when both deployments are in scope, so the human does one admission/speech window instead of two serialized checks.

## Delivery-Ready Machine Checkpoint — 2026-05-14T08:05Z

Current stage:

- `develop-human`
- Legal next states: `develop-code` or `stage`

Current running local tags:

- Compose state tag: `0.10.6-260514-0008`
- Lite state tag: `0.10.6-260514-0027`
- `vexa-dashboard-1` -> `vexaai/dashboard:0.10.6-260514-0008`
- `vexa-api-gateway-1` -> `vexaai/api-gateway:0.10.6-260514-0008`
- `vexa-meeting-api-1` -> `vexaai/meeting-api:0.10.6-260514-0008`
- `vexa-runtime-api-1` -> `vexaai/runtime-api:0.10.6-260514-0008`
- `vexa-admin-api-1` -> `vexaai/admin-api:0.10.6-260514-0008`
- `vexa-lite` -> `vexa-lite:0.10.6-260514-0027`

Fresh machine-verified handoff:

- `local-human-mechanical-gate`: pass.
- `dashboard-auth`: pass; compose direct login returns `test@vexa.ai`, cookie flags are correct, and `/api/vexa/meetings` returns 200.
- `dashboard-recording-playback-ready`: pass for `http://127.0.0.1:3001/meetings/10099`; browser playback rendered completed recording playback and not `Preparing audio...`.
- `LOCAL_HUMAN_BROWSER_HANDOFF_ENDPOINTS_SSOT`: pass for the same browser playback route.
- `LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT`: pass for meeting `10099` with `6` recording chunk(s) and `17` transcript segment(s).
- `SCOPE_LOCAL_PROOFS_ALL_GREEN`: `33` LOCAL scope proof cells green.
- Full `make release-validate LOCAL=1 SCOPE=tests3/releases/260508-v0.10.6.1/scope.yaml`: Release gate PASSED; report `tests3/reports/release-0.10.6-260514-0027.md`.

Human handoff URLs:

- Compose dashboard: `http://127.0.0.1:3001/login`
- Compose delivery/playback meeting: `http://127.0.0.1:3001/meetings/10099`
- Lite dashboard: `http://127.0.0.1:3100/login`

Remaining human job:

- Confirm the dashboards are usable in the browser.
- Confirm meeting `10099` transcript/playback looks and sounds correct.
- Confirm any audible `/speak` path if the signer wants a fresh sensory check after the playback fix.

## Advisory-Included Machine Checkpoint — 2026-05-14T11:16Z

GHSA-9wv6-78fw-fq5c was pulled into v0.10.6.1 and is now represented in the
release scope/registry.

Machine-verified additions:

- `PRE_RELEASE_SECURITY_DEPENDENCY_FLOORS`: pass in compose and lite.
- Dashboard lockfile resolves PostCSS to `8.5.10`.
- Transcription-service no longer installs `python-multipart`.
- Transcription-service upload route uses a bounded standard-library multipart
  parser and rejects oversized bodies while streaming.
- `python3 -m pytest services/transcription-service/tests/test_config.py -q`:
  `33 passed`.
- CPU transcription-service release image build passed, and
  `pip show python-multipart` inside that image returned package-not-found.
- Full `make release-validate LOCAL=1 SCOPE=tests3/releases/260508-v0.10.6.1/scope.yaml`
  passed again; report `tests3/reports/release-0.10.6-260514-0027.md`.
- `SCOPE_LOCAL_PROOFS_ALL_GREEN`: `35` LOCAL scope proof cells green.

This checkpoint does not replace human sensory validation. It narrows the
human job back to product behavior: dashboard usability, transcript/playback
judgment, and audible `/speak` if a fresh sensory check is desired.

## Autonomous Local Human-Gate Pass — 2026-05-14T17:00+03:00

Purpose: rerun the machine-owned part of the LOCAL develop-human gate after
the dashboard URL SSOT and silent post-meeting refresh fixes.

Git HEAD observed: `ed0837d`.

Machine-green evidence refreshed:

- `local-human-mechanical-gate`: all LOCAL target URLs, containers,
  transcription-lb topology, recent logs, lite recording files, compose env
  SSOT, test-container cleanup, and dropped relational recording tables passed.
- Compose `walkability-smoke`: auth round trip, meeting list data, detail API,
  transcription URL configured, TTS roundtrip, and login HTML all passed.
- Lite `walkability-smoke`: auth round trip, meeting list data, detail API,
  transcription URL configured, TTS roundtrip, and login HTML all passed.
- Dashboard SSOT static gate passed:
  `DASHBOARD_CONFIG_NO_STALE_LOCALHOST_DEFAULTS`,
  `DASHBOARD_REWRITES_REQUIRE_BUILD_SSOT`,
  `DASHBOARD_ADMIN_URL_EXPLICIT_SSOT`,
  `DASHBOARD_CLIENT_URLS_FROM_RUNTIME_CONFIG`.
- Compose browser auth passed:
  `http://127.0.0.1:3001/meetings` and
  `http://127.0.0.1:3001/meetings/10122` loaded authenticated.
- Lite browser auth passed:
  `http://127.0.0.1:3100/meetings` and
  `http://127.0.0.1:3100/meetings/201` loaded authenticated.
- Cross-deployment cookie isolation passed: `vexa-token-compose` and
  `vexa-token-lite` stayed isolated in one browser session.
- Compose playback/browser handoff passed for
  `http://127.0.0.1:3001/meetings/10122`: the page rendered completed
  recording playback and visible transcript text with `15` segment(s), not
  `Recording is processing...` or `Preparing audio...`.
- Lite stale-auth recovery passed: stale `vexa-token-lite` did not leak
  `Invalid API key` on `http://127.0.0.1:3100/meetings/170`.
- Required transcription smoke passed: deployed runtime
  `TRANSCRIPTION_SERVICE_URL=http://transcription-lb/v1/audio/transcriptions`
  returned HTTP 200 with `1` segment.

Fresh live Teams attempt:

- Meeting URL:
  `https://teams.microsoft.com/meet/346488203185895?p=OV6ieMkFX7xegl9A4R`
- Compose bot: `Vexa human gate compose 2026-05-14`, meeting `10123`.
- Lite bot: `Vexa human gate lite 2026-05-14`, meeting `202`.
- Both bots dispatched and clicked `Join now`, but both remained in the Teams
  waiting room and timed out waiting for `status=active` after 90 seconds.
- Cleanup DELETE requests were accepted for both deployments; DB rows reached
  `completed` with `0` recordings, so these are not transcript/playback
  approval artifacts.
- Reports:
  `tests3/.state-compose/reports/compose/auto-real-teams-human-gate-compose-20260514.json`
  and
  `tests3/.state-lite/reports/lite/auto-real-teams-human-gate-lite-20260514.json`.

Conclusion:

- Autonomous/browser/machine-owned LOCAL validation is green.
- A fresh live Teams admission/transcript artifact is blocked by lobby/admission
  despite the meeting being expected to auto-admit. This cannot be signed as a
  fresh live-human gate artifact.
- Current strategy dashboard also parks ordinary human gates while production
  is degraded by classified readiness/observability issues; do not mark the
  human gate signed from this autonomous run.

## Fresh Cross-Platform Human Validation — 2026-05-14T17:25+03:00

Human observation:

- Human confirmed bots were present and working in all three platforms:
  Google Meet, Teams, and Zoom.
- Human stopped the bots and confirmed recordings render well.

Exact machine-verified artifacts after stop:

| Deployment | Meeting | Platform | Status | Transcript Segments | Recording |
|---|---:|---|---|---:|---|
| compose | `10124` | Google Meet | `completed` | `52` | `1` finalized master recording |
| compose | `10125` | Teams | `completed` | `52` | `1` finalized master recording |
| compose | `10126` | Zoom | `completed` | `13` | `1` finalized master recording |
| lite | `203` | Google Meet | `completed` | `34` | `1` finalized master recording |
| lite | `204` | Teams | `completed` | `34` | `1` finalized master recording |
| lite | `205` | Zoom | `completed` | `26` | `1` finalized master recording |

Recording/finalizer evidence:

- All six meetings have `data.recordings[0].status = completed`.
- All six final audio media files have `is_final = true`.
- All six final audio media files have
  `finalized_by = recording_finalizer.master`.
- All six recordings expose `playback_url.audio`.
- No `meeting-*` containers for these six meetings remained running after stop.

This closes the prior fresh-live caveat for local human validation: the current
approval artifacts are the six exact meetings above, not the earlier blocked
Teams waiting-room attempt (`10123`/`202`).

## Staged Three-Deployment Teams Fixture Attempt — 2026-05-14T21:06+03:00

Fixture supplied for real tests:

- `https://teams.microsoft.com/meet/352264708677876?p=XSrfAjEaSykgucZAkx`

Machine setup:

- Lite gateway: `http://172.239.56.250:8056`
- Compose gateway: `http://172.239.56.127:8056`
- Helm gateway: `http://172.238.186.27:30056`
- Stale local token mirrors for lite/compose were replaced from the deployed
  databases; `/bots/status` returned HTTP 200 on all three gateways before
  dispatch.

Run evidence:

| Deployment | Dashboard URL | Meeting | Result |
|---|---|---:|---|
| lite | `http://172.239.56.250:3000/meetings/22` | `22` | failed gate: bot reached `awaiting_admission`, never `active` |
| compose | `http://172.239.56.127:3001/meetings/36` | `36` | failed gate: `stopped_before_admission` / self-initiated leave |
| helm | `http://172.238.186.27:30001/meetings/58` | `58` | failed gate: bot reached `awaiting_admission`, never `active` |

Report directory:

- `tests3/.state/reports/stage-human-teams-20260514-175937/`

Conclusion:

- Dispatch/auth/webhook setup was proven against all three staged deployments.
- This run is not human-gate approval evidence because none of the three bots
  reached active/admitted state, so no real speech, transcript, or recording
  finalization could be validated from this Teams fixture.
- Lite meeting `22` and helm meeting `58` were explicitly stopped after the
  failed admission window to avoid consuming bot slots.

## Staged Three-Deployment Teams Fixture Attempt 2 — 2026-05-14T21:20+03:00

Fixture supplied for real tests:

- `https://teams.microsoft.com/meet/324602960531985?p=2wvbGPow1GWkXcljHR`

Run evidence:

| Deployment | Dashboard URL | Meeting | Result |
|---|---|---:|---|
| lite | `http://172.239.56.250:3000/meetings/23` | `23` | pass: admitted active, recorded chunks, stopped, finalized |
| compose | `http://172.239.56.127:3001/meetings/37` | `37` | fail: `stopped_before_admission` before active |
| compose rerun | `http://172.239.56.127:3001/meetings/38` | `38` | fail: `stopped_before_admission` before active |
| helm | `http://172.238.186.27:30001/meetings/59` | `59` | pass: admitted active, recorded chunks, stopped, finalized |

Report directory:

- `tests3/.state/reports/stage-human-teams-20260514-180751/`

Conclusion:

- The fresh fixture validates staged live Teams behavior on lite and helm.
- Compose is the current stage-human blocker. It dispatched but exited before
  admission twice, so compose does not yet have a fresh staged Teams artifact
  proving active bot, speech capture, transcript delivery, stop, and finalized
  recording.

## Compose Teams Callback Regression Debug — 2026-05-14T21:45+03:00

Issue observed:

- Compose Teams meetings `37`, `38`, and `39` failed before active with
  `completion_reason=stopped_before_admission` and `failure_stage=requested`.
- Meeting-api logs showed bot `status_change` callbacks returning `403`.
- Production data check found no matching Teams loss pattern in the current
  production database over the last 30 days, so this was staged compose drift,
  not an established production loss pattern.

Fix applied:

- Compose-stage was running an old `vexaai/vexa-bot:dev` image created
  2026-05-10. The current release image created 2026-05-14 was loaded into the
  compose VM.
- `deploy/compose/docker-compose.yml` now wires `INTERNAL_API_SECRET` into
  `runtime-api`.
- `services/runtime-api/profiles.yaml` now forwards
  `INTERNAL_API_SECRET` into `meeting` and `browser-session` profile
  containers.
- `tests3/tests/static/deferred-transcribe-master-gate.sh` now asserts the
  callback secret is wired through meeting-api, compose runtime-api, and the
  runtime profiles.

Validation:

- Static gate passed:
  `BOT_STATUS_CALLBACK_HAS_INTERNAL_SECRET_FALLBACK`.
- Direct compose-stage auth smoke:
  without `X-Internal-Secret` => `403`; with the same secret => `404 Meeting
  session not found`, proving auth passed and only the fake session failed.
- Fresh compose Teams rerun:
  `tests3/.state/reports/stage-human-teams-20260514-214554/compose-teams-normal-fixed.json`.

Rerun result:

| Deployment | Dashboard URL | Meeting | Result |
|---|---|---:|---|
| compose | `http://172.239.56.127:3001/meetings/40` | `40` | callback regression fixed; bot reached `awaiting_admission` and remained in Teams waiting room until the 360s harness timeout |

Conclusion:

- The `requested`/`403` callback regression is fixed.
- Compose still does not have a fresh staged Teams human-gate approval artifact,
  because meeting `40` was never admitted and therefore could not prove speech,
  transcript delivery, stop, or finalized recording.
- Meeting `40` was stopped cleanly after the failed admission window and ended
  `completed` with `completion_reason=stopped`.

## Compose Teams Gate Retry With Browser Evidence — 2026-05-14T22:50+03:00

Fixture:

- `https://teams.microsoft.com/meet/324602960531985?p=2wvbGPow1GWkXcljHR`

Run evidence:

| Deployment | Dashboard URL | Meeting | Result |
|---|---|---:|---|
| compose | `http://172.239.56.127:3001/meetings/41` | `41` | dispatch OK; `requested -> joining -> awaiting_admission`; not admitted after 420s |

Machine observations:

- Bot container: `meeting-41-dab8f139`.
- Bot clicked `Continue on this browser`, set display name
  `stage-human-compose-teams-gate`, selected/defaulted computer audio, clicked
  `Join now`, and emitted `awaiting_admission`.
- Meeting-api accepted bot callbacks with `200 OK`; no recurrence of the
  `requested`/`403` regression.
- Container display screenshot captured from X11:
  `tests3/releases/260508-v0.10.6.1/compose-teams-meeting-41-waiting-room.png`.
- Screenshot text: `Hi, stage-human-compose-teams-gate. Someone will let you in
  when the meeting starts.`
- Harness report:
  `tests3/.state/reports/stage-human-teams-20260514-225028/compose-teams-normal-gate.json`.

Cleanup:

- The bot was stopped after the failed admission window.
- Meeting `41` ended `completed` with `completion_reason=stopped`.

Conclusion:

- This is now a hard external blocker for this fixture: compose has a healthy
  bot waiting in Teams, but the host did not admit it. The next valid human
  gate action is to admit `stage-human-compose-teams-gate` or provide a Teams
  fixture that truly bypasses the lobby; then rerun compose and require
  `active`, transcript segments, stop, finalized recording, and dashboard URL.

## Compose Teams Replacement Fixture — 2026-05-15T10:19+03:00

Fixture sequence:

- Initial fixture `https://teams.microsoft.com/meet/389797037736687?p=aQf6ubqKLjRV4Pv3zM`
  was replaced by the human while running. Meeting `42` was stopped cleanly.
- Replacement fixture:
  `https://teams.microsoft.com/meet/348115150234238?p=F3HT6LC1qcXKujIS9E`.

Run evidence:

| Deployment | Dashboard URL | Meeting | Result |
|---|---|---:|---|
| compose | `http://172.239.56.127:3001/meetings/43` | `43` | dispatch OK; `requested -> joining -> awaiting_admission`; not admitted after 420s |

Machine observations:

- Bot container: `meeting-43-1a98c3ff`.
- Bot clicked `Join now`, set display name
  `stage-human-compose-teams-gate-3`, and reported a live remote audio hook.
- Meeting-api accepted bot callbacks; no recurrence of the old
  `requested`/`403` regression.
- Screenshot captured from the bot display:
  `tests3/releases/260508-v0.10.6.1/compose-teams-meeting-43-waiting-room.png`.
- Screenshot text: `Hi, stage-human-compose-teams-gate-3. Someone will let you
  in shortly.`
- Harness report:
  `tests3/.state/reports/stage-human-teams-20260515-101950/compose-teams-normal-gate-3.json`.

Terminal state:

- Meeting `43` transitioned `awaiting_admission -> needs_human_help`.
- Meeting `43` completed with `completion_reason=awaiting_admission_timeout`.

Conclusion:

- Compose Teams software path is healthy through dispatch, callback auth,
  Teams prejoin, and waiting-room classification.
- This replacement fixture still cannot approve the human gate because no
  admission occurred, so no `active`, transcript segments, stop/finalized
  recording, or dashboard playback URL could be proven.

## Compose Teams Passing Human-Gate Artifact — 2026-05-15T11:35+03:00

Fixture:

- `https://teams.microsoft.com/meet/387224466952682?p=TxjFxNRaAWKmKG8dLF`

Run evidence:

| Deployment | Dashboard URL | Meeting | Result |
|---|---|---:|---|
| compose | `http://172.239.56.127:3001/meetings/44` | `44` | pass: admitted active, transcript segments, stopped, finalized server-side master |

Harness report:

- `tests3/.state/reports/stage-human-teams-20260515-113555/compose-teams-normal-gate-4.json`

Machine assertions:

- `BOT_DISPATCH_OK`: pass.
- `BOT_REACHED_ACTIVE`: pass.
- `BOT_DELETE_OK`: pass.
- `CALLBACK_TERMINAL_REACHED`: pass, status `completed`.
- `STATUS_COMPLETED_ON_NORMAL_STOP`: pass.
- `MEETING_HAS_RECORDING`: pass, `1` recording.
- `FINALIZE_MARKER_IS_SERVER_SIDE_MASTER`: pass.
- `STORAGE_PATH_AT_MASTER`: pass.
- `DOWNLOAD_URL_POINTS_AT_MASTER`: pass, presigned MinIO URL points at
  `/audio/master.webm`.
- `MASTER_SIZE_PLAUSIBLE`: pass, `3000103` bytes.
- `MASTER_DURATION_PLAUSIBLE`: pass, `188.7s`.
- `SEGMENT_FITS_AUDIO_TIMELINE`: pass.
- `NO_HALLUCINATION_PHRASES`: pass, checked `23` transcript segments.
- `CHUNK_RATE_PLAUSIBLE`: pass, `7` chunks / `188.7s`.
- `DASHBOARD_NO_DUPLICATE_MEETINGS`: pass, `44` meetings and `0` duplicates.

Conclusion:

- This closes the compose Teams stage-human machine artifact gap. The remaining
  human work is product judgment on the delivered dashboard URL:
  transcript visible/matches audio context, recording player playable/scrub-able,
  and no auth/session/API-key error.
