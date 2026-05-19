# Release Scope Map — 260508-v0.10.6.1

Generated: 2026-05-14T21:18+03:00

Purpose: map the release state back to the signed scope, with evidence and
remaining gate caveats separated. This is a review aid, not a human signoff.

Primary evidence:

- Scope: `tests3/releases/260508-v0.10.6.1/scope.md`
- Structured scope: `tests3/releases/260508-v0.10.6.1/scope.yaml`
- Latest aggregate registry report: `tests3/reports/release-0.10.6-260514-1952.md`
- Human brief: `tests3/releases/260508-v0.10.6.1/local-human-brief.md`
- Human evidence log: `tests3/releases/260508-v0.10.6.1/human-validation-evidence.md`
- Live staged Teams run: `tests3/.state/reports/stage-human-teams-20260514-180751/`

## Executive State

Machine registry is broadly green for the release image set
`0.10.6-260514-1952`. The compose Teams approval artifact is now present:
meeting `44` reached `active`, produced transcript segments, stopped cleanly,
and finalized a server-side master recording. The earlier compose failure was
real and is recorded: compose was running an old bot image and rejecting bot
status callbacks with `403`; that regression is fixed and covered by a static
registry assertion. Follow-up waiting-room attempts (`40`, `41`, `43`) are
retained as evidence but are superseded by the passing artifact `44`.

Current stage: `stage-human`.

Current hard caveat: none from machine validation. Human still must perform the
stage-human product review and sign in their own words; AI must not pre-fill
approval fields.

## Scope Item Map

| Scope item | Status | How it is proven | Caveat |
|---|---|---|---|
| `/speak` works in prod; TTS pod no longer crash-loops; bot honors provider | Done in registry | `WALKABILITY_TTS_SPEAK_ROUND_TRIP`, `TTS_PLAYBACK_HANDLES_WAV_AND_MP3_RESPONSES`, helm TTS pod checks, Dockerfile permission fixes | Human still needs sensory/audible confirmation if signing customer experience, not just API roundtrip |
| Multichunk recordings play end-to-end; dashboard reads master, not chunk-0 | Done in registry and local human evidence | `RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE`, `DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES`, `DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY`; completed local meetings `10124`-`10126` and `203`-`205` | Keep watching for any dashboard path that reintroduces media selection; prior code review already treated this as direct blocker |
| Recording integrity; finalizer single-writer for master path | Done in registry | `SINGLE_WRITER_FOR_RECORDING_MASTER_PATH`, `SERVER_SIDE_MASTER_FINALIZER_EXISTS`, `BOT_EXIT_CALLBACK_INVOKES_FINALIZER`, `FINALIZER_BEFORE_STATUS_FLIP` | Some lower-weight runtime fixture checks remain missing in DoD details, especially SIGKILL/deferred-transcribe cells |
| `browser_session` DELETE no longer stuck in `stopping` | Done in registry | Runtime callback durability/static checks, browser handoff/CDP checks, remote-browser feature gate `100%` | Existing staged browser-session containers may remain active; do not confuse them with failed Teams bots |
| Canonical `playback_url`; dashboard stops choosing | Done in registry | `recording-playback-url-canonical` checks and dashboard playback-ready browser check | Public `media_files` compatibility remains for one release by scope decision |
| Drop relational `recordings` + `media_files`; JSONB is canonical | Mostly done; report has one explicit missing helm proof | Compose proofs show tables not referenced/dropped; migration scripts `m331-drop-relational-recordings.py` / restore pair present | Latest scope table still shows `helm RECORDINGS_TABLE_DROPPED_IN_PROD` missing. Treat as audit caveat until a helm proof artifact exists or scope accepts compose-only evidence |
| GMeet fast-fail and waiting-room behavior | Done in registry | `GMEET_REJECTION_PAGE_FAST_FAIL_UNDER_30S`, `GMEET_WAITING_ROOM_EVICTION_RETRY_OR_FAIL_CLEAN` | Human-only GMeet validation already succeeded locally; staged three-deployment live fixture currently focuses Teams |
| Post-meeting webhooks fire once per session | Done in registry | `webhooks` feature gate `100%`; idempotency and envelope/HMAC/spoof checks | Webhook proof is machine-owned; human should not validate webhook internals |
| Voice-agent virtual camera initializes when `voice_agent_enabled=true` | Done in registry | `BOT_CONFIG_CAMERA_ENABLED_WHEN_VOICE_AGENT` compose + helm | Scope labels this a stop-gap ahead of #246; do not overstate as final BotConfig architecture |
| BYO TTS WAV + MP3 playback dispatch | Done in registry | `TTS_PLAYBACK_HANDLES_WAV_AND_MP3_RESPONSES` | Sensory in-meeting TTS remains human-judgment if signing product experience |
| Multilanguage TTS auto-language detection | Done in registry | `TTS_AUTO_LANG_PICKS_RIGHT_VOICE`, `TTS_NEW_LANG_VOICE_AUTO_DOWNLOAD_CACHED` | Machine proof, no human action needed unless signing audible quality |
| WebM master files include EBML duration / scrubber interactive | Covered through playback/finalizer path | Dashboard playback-ready check and finalized master evidence | If strict EBML-only proof is required, add or cite a dedicated ffprobe artifact |
| Env-gated billing dispatch-check | Done in registry | `BOT_CREATE_HONORS_DISPATCH_CHECK_DENY` and dispatch-check deny test | Off by default by scope; validate only gating behavior, not billing product |
| GHSA-9wv6-78fw-fq5c dependency blocker | Done in registry and Hardenloop scope | `PRE_RELEASE_SECURITY_DEPENDENCY_FLOORS` across compose/lite/helm; dashboard PostCSS floor; transcription-service without `python-multipart` | Hardenloop full bundled scanner has historical/noisy blockers; release-normalized scan reported `0` blockers |
| Teams Continue without AV modal | Done in registry | `TEAMS_CONTINUE_NO_AV_MODAL_DISMISSED`; Teams static greps include blue-square/captionless fallback budget | Fresh staged Teams live gate is separate and still compose-pending |
| Admin API Swagger header | Done in registry | `SWAGGER_CURL_EXAMPLE_SHOWS_CORRECT_HEADER` | Docs-only |
| Apple-Silicon vexa-lite docs | Done in registry | `VEXA_LITE_APPLE_SILICON_CAVEAT_DOCUMENTED` | Verified arm64 runtime remains deferred to v0.10.7 |
| Narrow broad exception in `callbacks.finalize` | Done in registry | `CALLBACKS_FINALIZE_NARROW_EXCEPT` | Internal hardening |
| Accurate prior chunk count log | Done in registry | `CHUNK_WRITE_PRIOR_COUNT_LOG_ACCURATE` | Internal/debuggability |
| Backlog issue audit sweep | Done by scope report | Scope status marks pass | Evidence is release-doc/process, not runtime |
| Migration convention README | Done | `tests3/lib/migrations/README.md` present | Full schema drift detection deferred to v0.10.7 |

## Live Human-Gate Map

| Deployment | Fixture | Meeting | Current result |
|---|---|---:|---|
| lite | Teams `324602960531985` | `23` | Passed autonomous live cell: admitted, recorded chunks, stopped, finalized |
| helm | Teams `324602960531985` | `59` | Passed autonomous live cell: admitted, recorded chunks, stopped, finalized |
| compose | Teams `324602960531985` | `37` | Failed first attempt: `stopped_before_admission` |
| compose | Teams `324602960531985` | `38` | Failed rerun: `stopped_before_admission` before reaching active |
| compose | Teams `324602960531985` | `40` | Callback regression fixed; bot reached `awaiting_admission`, then timed out waiting-room admission after 360s |
| compose | Teams `324602960531985` | `41` | Callback regression fixed; screenshot-backed Teams waiting room; timed out after 420s |
| compose | Teams `348115150234238` | `43` | Callback regression fixed; screenshot-backed Teams waiting room; terminal reason `awaiting_admission_timeout` |
| compose | Teams `387224466952682` | `44` | Passed autonomous live cell: admitted, transcript segments, stopped, finalized master |

Human-facing URLs:

- Lite: `http://172.239.56.250:3000/meetings/23`
- Compose first attempt: `http://172.239.56.127:3001/meetings/37`
- Compose rerun: `http://172.239.56.127:3001/meetings/38`
- Compose fixed-callback rerun: `http://172.239.56.127:3001/meetings/40`
- Compose screenshot-backed rerun: `http://172.239.56.127:3001/meetings/41`
- Compose replacement fixture: `http://172.239.56.127:3001/meetings/43`
- Compose passing artifact: `http://172.239.56.127:3001/meetings/44`
- Helm: `http://172.238.186.27:30001/meetings/59`

## Review Findings

1. The release is not blocked by general registry coverage; deployment coverage
   is green with no failed reports in the latest aggregate.
2. The prior stage-human blocker is closed by compose meeting `44`, which is
   the current admitted Teams live cell.
3. The helm proof gap for dropped relational recording tables should be closed
   or explicitly accepted before audit, because the scope table still calls it
   missing even though the aggregate deployment coverage is otherwise green.
4. Human checklist should remain product-only: login, create/join, bot visible,
   speech appears as transcript, stop leaves finalized recording playback,
   and no auth/session/API-key errors. Everything else belongs to registry.
