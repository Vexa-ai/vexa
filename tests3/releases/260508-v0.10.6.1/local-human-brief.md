## TL;DR

This is the LOCAL `develop-human` walkthrough for `260508-v0.10.6.1`.

Human job: use the dashboard like a real customer and confirm the product actually works.

AI/operator job: watch logs, API state, DB state, and registry evidence. The human should not debug ports, inspect containers, run curl, or prove machine-checkable conditions.

Login identity for both dashboards: `test@vexa.ai`.

Current machine checkpoint, refreshed 2026-05-14T21:06+03:00:

- Current stage: `stage-human`.
- Image set under validation: `0.10.6-260514-1952` for helm and current staged `:dev` services.
- Lite dashboard: `http://172.239.56.250:3000/login`.
- Compose dashboard: `http://172.239.56.127:3001/login`.
- Helm dashboard: `http://172.238.186.27:30001/login`.
- Login identity for all three dashboards: `test@vexa.ai`.
- Registry deployment coverage is green: compose `52` reports / `0` failed, lite `29` reports / `0` failed, helm `30` reports / `0` failed.
- Feature confidence gates are green, including infrastructure `100%`, dashboard `90%`, webhooks `100%`, security hygiene `100%`, and remote browser `100%`.
- Transcription service smoke is green from the deployed runtime URL/token: HTTP 200 with non-empty transcript segments.
- Dashboard/auth/CORS/WebSocket/CDP checks are green across the staged deployments.
- Test account bot limit is canonical at `3`; stale slot consumers were cleaned before handoff.
- Synthetic internal session-bootstrap is intentionally not exposed through the public gateway; the synthetic pack records this hardened skip/pass instead of consuming bot slots.
- Advisory dependency proof is green after GHSA-9wv6-78fw-fq5c was pulled into scope; dashboard PostCSS is `8.5.14`, and transcription-service no longer installs `python-multipart`.
- Hardenloop release scan remains `ready`, with `0` release blockers.
- Current aggregate report: `tests3/reports/release-0.10.6-260514-1952.md`.
- Fresh staged Teams fixture attempted: `https://teams.microsoft.com/meet/352264708677876?p=XSrfAjEaSykgucZAkx`.
- Fresh staged Teams result: not approved yet. Lite meeting `22` and helm meeting `58` reached `awaiting_admission` but not `active`; compose meeting `36` failed with `stopped_before_admission`. Evidence lives in `tests3/.state/reports/stage-human-teams-20260514-175937/`.

Concise assurance for this human delivery:

- Expected: login works on all three dashboards, meeting list/detail pages load, creating a fresh meeting works, a bot can join when admitted, transcript text appears from real speech, stop/finalization leaves recording playback available, and the UI does not show auth/session/API-key failures.
- Not expected: the human does not validate tokens, CORS, WebSockets, CDP proxying, dropped-table state, container health, MinIO/recording internals, TTS pod readiness, or dependency floors; those are registry-covered.
- Known boundary: Teams captions may be withheld from bots by tenant policy. If captions are unavailable, judge the visible bot join/recording/finalization behavior and note that transcript delivery depends on the configured audio/caption path for that meeting.
- Current blocker for the fresh staged Teams gate: the supplied Teams fixture did not admit bots to active during the automated observation window, so it cannot yet serve as a human approval artifact.

---

# Delivery Assurance

## Expected From Human

- Open the listed local dashboard URLs and use them like a customer.
- Confirm login, meeting navigation, transcript readability, recording playback, and audible speak/TTS behavior feel correct.
- For any fresh real-meeting run, admit/listen/judge the bot experience while AI/operator verifies the exact meeting artifact.
- Sign checklist items only when the visible product behavior and the machine-observed artifact agree.

## Not Expected From Human

- Do not debug ports, containers, logs, env vars, registry IDs, scanner output, or API status codes.
- Do not prove auth internals, transcription token plumbing, dependency floors, CORS/env parity, container health, dropped tables, or stale-error recovery; registry tests already covered those.
- Do not use quarantined/pre-fix evidence as approval evidence.
- Do not accept "it was green earlier" for a new real-meeting item; new human interaction needs its own fresh artifact check.

## Already Assured By Registry And Harness

- Local targets are reachable and walkable for lite and compose.
- Auth works without localhost cookie collision or raw stale-token errors.
- Transcription token plumbing is single-source-of-truth and has no placeholder fallback.
- Completed compose meeting `10122` renders visible transcript text and playback controls.
- Fresh compose/lite cross-platform meetings render completed recordings after stop: compose `10124`/`10125`/`10126`; lite `203`/`204`/`205`.
- Post-meeting artifact refresh is silent; it does not reload the page or visibly churn the loading state.
- Fresh transcription service smoke returned HTTP 200 with non-empty segments from the deployed runtime URL/token.
- Security/dependency release gates are green, including Hardenloop `ready` with `0` release blockers.
- Registry sweep is green: all `143` checks pass.

# Human Walkthrough

## Quarantined Pre-Fix Evidence

Do not use meeting `171` as human approval evidence.

Meeting `171` was created before the lite transcription token fix. The bot recorded chunks and detected speech, but every live transcription request returned `HTTP 401 Invalid or missing API token`. After the required lite redeploy, the old container-local recording files were gone, so deferred transcription cannot recover that meeting.

For the local handoff, the stale lite rows that advertised missing recording files were backed up and quarantined from the active lite validation DB. The remaining lite gate is browser/auth sanity plus a token-aware transcription-service smoke; the real admitted-bot transcript proof is compose-only, where meeting evidence exists.

## 1. Lite dashboard sanity check

Target: `http://127.0.0.1:3100/login`

Human does:

1. Open `http://127.0.0.1:3100/login`.
2. Log in with `test@vexa.ai`.
3. Open `http://127.0.0.1:3100/meetings`.
4. Open a post-fix meeting detail page. Do not use meeting `171` as approval evidence.
5. Confirm the page does not show `Authentication failed`, `Invalid API key`, or `Something went wrong`.
6. Confirm the meeting detail page is understandable: status, transcript/recording sections, meeting metadata, and controls are visible where expected.

Human approves if:

- Login works without surprise re-auth prompts.
- `/meetings` and a post-fix meeting detail page are usable.
- Any transcript currently present on the post-fix meeting is visible and coherent.
- No customer-facing error banner appears.

AI/operator observes:

- No new `vexa-lite` runtime errors during the navigation.
- Dashboard auth uses `vexa-token-lite`, not the compose cookie.
- The selected post-fix meeting detail API returns `200` for the logged-in session.

## 2. Compose dashboard sanity check

Target: `http://127.0.0.1:3001/login`

Human does:

1. Open `http://127.0.0.1:3001/login`.
2. Log in with `test@vexa.ai`.
3. Open `http://127.0.0.1:3001/meetings`.
4. Open at least one meeting detail page from the list.
5. Open meeting `10122` and confirm transcript/recording state matches what the UI claims.
6. Confirm the recording player is usable. Automation already proved this page renders playback instead of `Recording is processing...` or `Preparing audio...`; the human check is only whether the playback experience works as expected.
7. Confirm the page does not show `Authentication failed`, `Invalid API key`, or `Something went wrong`.

Human approves if:

- Login and meeting navigation work in compose after also using lite.
- `/meetings` and a meeting detail page are usable.
- Transcript/recording affordances make sense for the meeting state.
- No cookie collision or cross-deployment auth confusion is visible.

AI/operator observes:

- Compose dashboard uses `vexa-token-compose`, not the lite cookie.
- `api-gateway`, `meeting-api`, `runtime-api`, `dashboard`, and `tts-service` emit no new errors.
- Browser auth remains valid after both localhost deployments are used in the same browser.

## 3. Fresh Google Meet transcription flow

Target: compose dashboard, `http://127.0.0.1:3001/meetings`

Human does:

1. Create or open a real Google Meet.
2. In compose dashboard, click `Join Meeting`.
3. Paste the Google Meet URL.
4. Keep platform as Google Meet.
5. Keep transcription enabled.
6. Use bot name `Vexa human gate` or another recognizable name.
7. Start transcription.
8. Admit the bot in Google Meet if the meeting asks.
9. Talk naturally for at least 30-60 seconds. Include a few distinct phrases that will be easy to recognize later.
10. Watch the dashboard meeting detail page while speaking.
11. Confirm transcript text appears during the meeting or shortly after.
12. Stop the bot from the dashboard.
13. Confirm the meeting transitions out of active/stopping and keeps transcript access after stop.

Human approves if:

- The bot joins the real meeting.
- The meeting status transitions are understandable.
- Spoken words appear as transcript text.
- Transcript remains available after the bot is stopped.
- The UI feels like a working customer flow, not just a raw API demo.

AI/operator observes:

- Runtime creates a bot container and it reaches the expected status transitions.
- Meeting API records transcript segments for the new meeting.
- Transcription service is called successfully.
- No callback/finalizer/webhook errors appear while the human walks the flow.

## 4. Recording playback and finalization

Target: the fresh compose meeting from step 3, or another completed multi-chunk meeting.

Human does:

1. After the meeting stops, open the meeting detail page.
2. If the recording is still finalizing, confirm the UI says that clearly instead of showing a broken/blank player.
3. When playback is available, press play.
4. Scrub forward and backward.
5. Confirm playback is audible and covers the expected meeting duration instead of only the first chunk.

Human approves if:

- Finalizing state is clear while the recording is not ready.
- Playback becomes available after finalization.
- Audio is audible.
- Scrubbing works.
- Playback does not truncate to an early chunk.

AI/operator observes:

- Recording data includes a `playback_url` after finalization.
- Finalizer logs progress without errors.
- Dashboard does not fall back to deleted relational `recordings` / `media_files` tables.

## 5. Speak / TTS audible check

Target: compose dashboard, same real meeting if still active or a fresh one.

Human does:

1. Use the dashboard speak flow or `/speak` equivalent.
2. Send a short phrase such as: `This is the Vexa human gate audio check`.
3. Listen in the meeting.

Human approves if:

- The phrase is actually heard in the meeting.
- Audio is not silence, static, or corrupted.
- The UI/API reports success in a way that matches what was heard.

AI/operator observes:

- TTS service returns audio successfully.
- Bot playback logs show successful synthesis/playback.
- No response-format or playback errors appear.

# Optional Platform Edge Checks

These are required only if the human wants to exercise the platform-specific checklist items before advancing:

- Teams: dispatch a Teams bot where the Continue-without-AV modal appears; confirm the modal is dismissed and the bot proceeds.
- Google Meet host-not-started: dispatch a bot to a meeting whose host has not started; confirm it fails quickly with a clear classified reason.
- Voice-agent camera: dispatch with `voice_agent_enabled=true`; confirm the virtual camera initializes and the meeting experience matches the intended behavior.

# What Human Should Not Test Manually

Already automated and green:

- URL reachability.
- Container health.
- Recent service log cleanliness before the checkpoint.
- `/meetings` browser auth.
- Meeting-detail auth and stale `Invalid API key` cleanup.
- Cross-deployment localhost cookie isolation.
- Completed recording playback readiness for meeting `10122`: real browser loaded `http://127.0.0.1:3001/meetings/10122`, rendered completed recording playback and visible transcript text with `15` segment(s), and did not show `Recording is processing...` or `Preparing audio...`.
- Dashboard URL SSOT: no stale `localhost:8066`/`localhost:18056`/`ws://localhost:3001`, no runtime rewrite patching, admin URLs require explicit `VEXA_ADMIN_API_URL`, and browser helpers derive URLs from runtime config/request origin.
- Transcription service smoke: `SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP`.

# Known Caveat For This Handoff

The autonomous fresh Teams run is blocked on meeting admission, not on registry plumbing:

- Compose `10123` and lite `202` both dispatched to `https://teams.microsoft.com/meet/346488203185895?p=OV6ieMkFX7xegl9A4R`.
- Both clicked `Join now`, then remained in the Teams waiting room and timed out waiting for `active`.
- Both were stopped/cleaned up and completed with `0` recordings.

If the human wants a fresh live-meeting approval artifact, use a meeting where the bot visibly reaches `active`, then speak for 30-60 seconds and let the AI/operator verify the exact new meeting ID before signing.

Resolved by later fresh cross-platform run:

- Compose `10124` Google Meet: `completed`, `52` transcript segment(s), finalized playback recording.
- Compose `10125` Teams: `completed`, `52` transcript segment(s), finalized playback recording.
- Compose `10126` Zoom: `completed`, `13` transcript segment(s), finalized playback recording.
- Lite `203` Google Meet: `completed`, `34` transcript segment(s), finalized playback recording.
- Lite `204` Teams: `completed`, `34` transcript segment(s), finalized playback recording.
- Lite `205` Zoom: `completed`, `26` transcript segment(s), finalized playback recording.
- Local deployment parameter SSOT: `deploy/compose/.env` owns transcription URL/token for both compose and lite; smoke proves deployed runtime env matches that file.
- Transcription-lb/worker readiness.
- `vexa-lite` memory below 2 GiB.
- Compose env parity.
- Absence of leftover test containers.
- Absence of relational `recordings` / `media_files` tables.

# Observer Commands

AI/operator watches these while the human walks:

- `docker logs --since 0m -f vexa-lite`
- `docker compose -f deploy/compose/docker-compose.yml logs -f api-gateway meeting-api runtime-api dashboard tts-service`
- `docker ps --format '{{.Names}} {{.Status}}'`

# Automated Evidence

Green:

- `DASHBOARD_BROWSER_MEETINGS_AUTH_OK`: lite and compose `/meetings` plus meeting detail pages load in a real browser.
- `DASHBOARD_DETAIL_STALE_AUTH_RECOVERS`: stale detail-page auth does not leak raw `Invalid API key`.
- `DASHBOARD_AUTH_COOKIES_ISOLATED`: lite and compose can both be used on `localhost` without cookie collision.
- `DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY`: real browser proof for meeting `10122`; completed recording rendered playback controls, visible transcript text with `15` segment(s), and did not render `Recording is processing...` or `Preparing audio...`.
- `DASHBOARD_TRANSCRIPT_RENDERED_VISIBLE`: compose meeting `10122` rendered visible transcript text in the browser.
- `DASHBOARD_CONFIG_NO_STALE_LOCALHOST_DEFAULTS`, `DASHBOARD_REWRITES_REQUIRE_BUILD_SSOT`, `DASHBOARD_ADMIN_URL_EXPLICIT_SSOT`, `DASHBOARD_CLIENT_URLS_FROM_RUNTIME_CONFIG`: dashboard URL SSOT is machine-verified.
- `AUTO_REAL_TEAMS_HUMAN_GATE`: attempted but not green; compose `10123` and lite `202` stayed in the Teams waiting room and are not approval artifacts.
- `COMPOSE_TEAMS_CALLBACK_REGRESSION_FIXED`: staged compose was rejecting bot status callbacks from an old bot image / missing runtime secret wiring. The current release bot image is loaded on compose-stage, `INTERNAL_API_SECRET` is wired through compose runtime-api and runtime profiles, and the fixed rerun reached `awaiting_admission` instead of failing at `requested`.
- `COMPOSE_TEAMS_WAITING_ROOM_SCREENSHOT`: compose meeting `41` captured the bot browser in Teams waiting room (`Someone will let you in...`). Human gate cannot approve compose Teams until the bot is actually admitted or a no-lobby fixture is supplied.
- `COMPOSE_TEAMS_REPLACEMENT_FIXTURE_TIMEOUT`: compose meeting `43` captured the bot browser in Teams waiting room for the replacement fixture and completed as `awaiting_admission_timeout`; this is not approval evidence.
- `COMPOSE_TEAMS_PASSING_STAGE_HUMAN_ARTIFACT`: compose meeting `44` reached active, produced transcript segments, stopped cleanly, finalized a server-side master recording, and is the current dashboard URL for human review: `http://172.239.56.127:3001/meetings/44`.
- `SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP`: transcription service returns `HTTP 200` plus non-empty segment(s).
- `TRANSCRIPTION_TOKEN_NO_PLACEHOLDER_FALLBACK`: local handoff deploy/smoke paths use `deploy/compose/.env` as the transcription URL/token SSOT and contain no local/dev/example token fallback, caller-env override, or host/generated-env token scavenging.
- `LOCAL_HUMAN_*`: target URLs, containers, logs, memory, env parity, cleanup, and dropped tables are machine-verified.

# Signoff Artefacts

- Checklist: [local-human-checklist.yaml](/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/local-human-checklist.yaml)
- Scope sign block: [scope.md](/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/scope.md)
