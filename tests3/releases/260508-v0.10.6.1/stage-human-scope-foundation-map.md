# Stage-Human Scope + Foundation Test Map

Generated: 2026-05-15T11:40+03:00

Purpose: define exactly which machine tests must be green before delivery to
the human, how those tests map to the signed scope, and what remains
human-only. This file is evidence/planning only; it does not set any
`approved: true` field.

## Current Stage

- Current stage: `stage-human`.
- Current live compose Teams artifact: meeting `44`.
- Candidate fixture:
  `https://teams.microsoft.com/meet/387224466952682?p=TxjFxNRaAWKmKG8dLF`.
- Candidate bot: `stage-human-compose-teams-gate-4`.
- Artifact status: `PASS`; meeting reached `active`, completed on normal stop,
  produced transcript segments, and finalized a server-side master recording.

## Foundation Tests Before Human Delivery

These are the foundation tests that must be green before the human receives
URLs. The human should not manually re-test these mechanics.

| Foundation area | Required checks | Why it gates human delivery |
|---|---|---|
| Scope proof coverage | `SCOPE_LOCAL_PROOFS_ALL_GREEN` | Prevents a blind handoff where scope proof cells exist but were never run. |
| Deployment walkability | `LOCAL_HUMAN_TARGET_URLS_READY`, `LOCAL_HUMAN_CONTAINERS_HEALTHY`, `LOCAL_HUMAN_RECENT_LOGS_CLEAN`, `LOCAL_HUMAN_MEMORY_WITHIN_LIMIT`, `LOCAL_HUMAN_COMPOSE_ENV_SSOT`, `LOCAL_HUMAN_NO_TEST_CONTAINERS` | Proves the handoff URLs and local/staged stack are mechanically usable before human time is spent. |
| Dashboard auth/session | `DASHBOARD_BROWSER_MEETINGS_AUTH_OK`, `DASHBOARD_DETAIL_STALE_AUTH_RECOVERS`, `DASHBOARD_AUTH_COOKIES_ISOLATED` | Covers the reported login/session/API-key failure class. |
| Dashboard URL SSOT | `DASHBOARD_CONFIG_NO_STALE_LOCALHOST_DEFAULTS`, `DASHBOARD_REWRITES_REQUIRE_BUILD_SSOT`, `DASHBOARD_ADMIN_URL_EXPLICIT_SSOT`, `DASHBOARD_CLIENT_URLS_FROM_RUNTIME_CONFIG` | Covers stale baked localhost/websocket/proxy drift. |
| Test-account limits | `TEST_ACCOUNT_BOT_LIMIT_IS_CANONICAL`, `TEST_ACCOUNT_NO_STALE_SLOT_CONSUMERS` | Ensures `test@vexa.ai` can run the human gate without hidden stale slot blockers. |
| Transcription service path | `SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP`, `TRANSCRIPTION_TOKEN_NO_PLACEHOLDER_FALLBACK`, `LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT` | Covers "chunks but no transcript" and "fake/local token fallback" failure classes. |
| Internal callback auth | `BOT_STATUS_CALLBACK_HAS_INTERNAL_SECRET_FALLBACK`, `RUNTIME_EXIT_CALLBACK_SENDS_INTERNAL_SECRET` | Covers the compose `403 status_change -> requested -> stopped_before_admission` regression. |
| Recording finalization | `RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE`, `DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY`, `LOCAL_HUMAN_BROWSER_HANDOFF_ENDPOINTS_SSOT` | Covers "recording processing forever", chunk-0 playback, and non-browser-reachable recording URLs. |
| Recording architecture | `DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES`, `SINGLE_WRITER_FOR_RECORDING_MASTER_PATH`, `SERVER_SIDE_MASTER_FINALIZER_EXISTS`, `BOT_EXIT_CALLBACK_INVOKES_FINALIZER`, `FINALIZER_BEFORE_STATUS_FLIP` | Enforces JSONB/canonical playback and finalizer single-writer semantics. |
| Teams prejoin behavior | `TEAMS_CONTINUE_NO_AV_MODAL_DISMISSED`, live Teams gate `BOT_REACHED_ACTIVE` | Covers the Teams Continue/no-AV modal and confirms a real bot enters a real meeting. |
| Security release blocker | `PRE_RELEASE_SECURITY_DEPENDENCY_FLOORS` plus Hardenloop normalized result | Covers GHSA-9wv6-78fw-fq5c release-built surfaces before human delivery. |
| TTS/speak foundation | `WALKABILITY_TTS_SPEAK_ROUND_TRIP`, `TTS_PLAYBACK_HANDLES_WAV_AND_MP3_RESPONSES`, `BOT_SPEAK_HONORS_PROVIDER_PARAM` | Machine-proves API/playback plumbing; human only judges audible experience if required. |

## Scope Item Mapping

| Signed scope item | Foundation / registry coverage | Live/human gate coverage |
|---|---|---|
| Multichunk recordings play end-to-end; dashboard reads master, not chunk-0 | `RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE`, `DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES`, `DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY`, `LOCAL_HUMAN_BROWSER_HANDOFF_ENDPOINTS_SSOT` | Human opens the delivered meeting URL and confirms recording is playable, scrub-able, full-duration, and not stuck at processing. |
| Recording integrity; finalizer is sole writer of master path | `SINGLE_WRITER_FOR_RECORDING_MASTER_PATH`, `SERVER_SIDE_MASTER_FINALIZER_EXISTS`, `BOT_EXIT_CALLBACK_INVOKES_FINALIZER`, `FINALIZER_BEFORE_STATUS_FLIP`, live stop/finalize checks in `autonomous_real_meeting.py` | Human only confirms the rendered product result; machine owns finalizer correctness. |
| Canonical `playback_url`; dashboard stops choosing media files | `DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES`, `RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE`, `DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY` | Human sees a dashboard recording player sourced from the canonical dashboard route. |
| Relational `recordings` + `media_files` dropped / JSONB canonical | `LOCAL_HUMAN_RECORDINGS_TABLES_DROPPED`, `RECORDINGS_TABLE_NOT_REFERENCED`, `MEDIA_FILES_TABLE_NOT_REFERENCED`, migration scripts `m331-*` | No human action; audit caveat remains if a specific helm proof is required. |
| `browser_session` DELETE no longer stuck in `stopping` | `RUNTIME_API_DELETE_EMITS_EXIT_CALLBACK`, `STUCK_MEETING_SWEEP_USES_PROGRESS_TIMESTAMP`, `RUNTIME_EXIT_CALLBACK_SENDS_INTERNAL_SECRET` | No human action unless validating browser-session product UX. |
| Teams Continue without AV modal handled | `TEAMS_CONTINUE_NO_AV_MODAL_DISMISSED`, live Teams bot logs showing prejoin and `Join now` success | Human admits/observes the bot if Teams lobby is present; machine owns modal/prejoin mechanics. |
| Real transcription pipeline works before handoff | `SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP`, `LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT`, live Teams gate segment assertions | Human speaks and confirms visible transcript text in the delivered meeting. |
| No placeholder/fallback token pattern | `TRANSCRIPTION_TOKEN_NO_PLACEHOLDER_FALLBACK`, `SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP` SSOT checks | No human action. |
| Dashboard auth/session/API-key failures fixed | `DASHBOARD_BROWSER_MEETINGS_AUTH_OK`, `DASHBOARD_DETAIL_STALE_AUTH_RECOVERS`, `DASHBOARD_AUTH_COOKIES_ISOLATED` | Human logs in with `test@vexa.ai` and confirms no auth/session/API-key error appears. |
| Dashboard URL/proxy/websocket SSOT | `DASHBOARD_CONFIG_NO_STALE_LOCALHOST_DEFAULTS`, `DASHBOARD_REWRITES_REQUIRE_BUILD_SSOT`, `DASHBOARD_ADMIN_URL_EXPLICIT_SSOT`, `DASHBOARD_CLIENT_URLS_FROM_RUNTIME_CONFIG` | Human should not see stale localhost/proxy failure; if seen, bounce to develop-code. |
| Test account bot limit canonical | `TEST_ACCOUNT_BOT_LIMIT_IS_CANONICAL`, `TEST_ACCOUNT_NO_STALE_SLOT_CONSUMERS` | Human should be able to create the required bot; no manual DB checking. |
| `/speak` and BYO TTS playback | `WALKABILITY_TTS_SPEAK_ROUND_TRIP`, `BOT_SPEAK_HONORS_PROVIDER_PARAM`, `TTS_PLAYBACK_HANDLES_WAV_AND_MP3_RESPONSES` | Optional human audible check: send phrase and confirm it is heard in-meeting. |
| GHSA-9wv6-78fw-fq5c dependency blocker | `PRE_RELEASE_SECURITY_DEPENDENCY_FLOORS`, Hardenloop validate-candidate normalized `0` blockers | No human action beyond accepting the audit evidence. |
| Env-gated billing dispatch-check | `BOT_CREATE_HONORS_DISPATCH_CHECK_DENY`, `BOT_CREATE_NOOP_WHEN_DISPATCH_CHECK_UNSET`, `ADMIN_BOT_CREATE_HONORS_DISPATCH_CHECK_DENY` | No human action; feature is default-disabled/foundation only. |
| Admin API Swagger header | `SWAGGER_CURL_EXAMPLE_SHOWS_CORRECT_HEADER` | No human action unless reviewing docs. |
| Apple Silicon vexa-lite caveat | `VEXA_LITE_APPLE_SILICON_CAVEAT_DOCUMENTED` | No human action; runtime support deferred to v0.10.7. |

## Live Teams Gate: Pass Definition

The compose Teams live gate is the missing product artifact. It is not enough
to dispatch a bot or reach the waiting room.

Pass requires all of:

- `BOT_DISPATCH_OK`: `/bots` accepts the request and returns a meeting id.
- `BOT_REACHED_ACTIVE`: meeting transitions to `active`.
- transcript evidence: non-empty transcript segments are persisted after
  real meeting audio.
- recording evidence: chunks are present during the run.
- stop evidence: bot leaves cleanly and meeting reaches a terminal state.
- finalizer evidence: a finalized master recording exists with playback URL.
- dashboard evidence: delivered URL opens for human review.

Approved machine artifact:

- Compose meeting `44`.
- Dashboard URL:
  `http://172.239.56.127:3001/meetings/44`.
- Harness report path:
  `tests3/.state/reports/stage-human-teams-20260515-113555/compose-teams-normal-gate-4.json`.
- Result: `17` pass / `0` fail / `2` skip.
- Transcript: `23` checked segments.
- Recording: `1` recording, finalized by `recording_finalizer.master`.
- Master: `3000103` bytes, `188.7s`, storage path points at
  `/audio/master.webm`.

## Human-Only Checklist After Machine Pass

Human receives only product-judgment work:

- Log in with `test@vexa.ai`.
- Open the exact delivered dashboard meeting URL.
- Confirm transcript text is visible and matches spoken/audio context.
- Confirm recording player is present, playable, scrub-able, and not stuck at
  `processing`.
- Confirm no auth/session/API-key error appears.
- Optional: use `/speak`/dashboard speak and confirm the phrase is actually
  heard in the meeting.

If any of these fail, bounce to `develop-code`; do not mark the human gate
approved.
