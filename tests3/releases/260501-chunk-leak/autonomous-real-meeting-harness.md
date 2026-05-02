# Autonomous real-meeting harness — v0.10.2 → v0.10.6 regression coverage

Purpose: end-to-end exercise of every customer-visible change since v0.10.2,
driven by real meeting URLs, with all assertions auto-verified — so the
human gate is reduced to UI-only confirmation.

## Usage

You (the operator) host one meeting per platform you want covered. Run:

```bash
tests3/tests/autonomous-real-meeting.sh \
  --gmeet=https://meet.google.com/abc-defg-hij \
  --teams='https://teams.microsoft.com/meet/<id>?p=<passcode>' \
  --zoom='https://us04web.zoom.us/j/<id>?pwd=<passcode>' \
  --deployments=compose,helm,lite \
  --mode=normal \
  --duration=240
```

You can omit any URL (only test the platforms you have URLs for) and any
deployment (skip cells you don't have endpoints for). Default duration is
240s — long enough for a few transcript segments + a chunk-leak slope fit
without burning your meeting host time.

The script forks one bot per (platform × deployment) cell. Each bot uses a
distinct `--bot-name` so you can identify which bot to admit in the
host UI.

After dispatch, the script prints `ADMIT BOT NOW` lines per-cell. Admit
each bot as it appears. Each cell waits up to 180s for `status=active`,
then records for `--duration`, terminates per `--mode`, and runs the full
assertion bank.

Reports land at `tests3/.state/reports/auto-real-<ts>/<cell>.json`, plus
an `aggregate.json` with the cross-cell verdict.

## Modes

- `--mode=normal` — clean DELETE /bots; expects `status=completed`. Pack C
  (user-stop classifier) lives here.
- `--mode=crash` — SIGKILL the bot pod/container mid-recording. Crash-safety
  path. Pre-Pack-U Zoom = total audio loss; this asserts master is still
  built and playable from chunks already in MinIO.

To get full coverage, run the script twice — once `normal`, once `crash`.

## Assertion catalogue (per cell)

Each cell produces a JSON report with these assertions. `pass` / `fail` /
`skip` per assertion; verdict is `pass` only when zero `fail`.

| ID                                      | Pack/Issue                  | What                                                                                       |
|-----------------------------------------|-----------------------------|--------------------------------------------------------------------------------------------|
| BOT_DISPATCH_OK                         | bot-lifecycle               | POST /bots returned 200 + bot id                                                          |
| BOT_REACHED_ACTIVE                      | bot-lifecycle               | meeting.status reached `active` within 180s of admission signal                           |
| BOT_DELETE_OK                           | bot-lifecycle (normal)      | DELETE /bots returned 200                                                                  |
| BOT_SIGKILL_OK                          | bot-lifecycle (crash)       | bot container/pod killed via docker kill / kubectl delete --grace-period=0                |
| CALLBACK_TERMINAL_REACHED               | runtime-api callback delivery | meeting.status reached completed/failed within 180s of stop                              |
| STATUS_COMPLETED_ON_NORMAL_STOP         | Pack C                      | normal-mode meeting ends `completed` (NOT failed — Pack C user-stop classifier)           |
| STATUS_TERMINAL_ON_CRASH                | runtime-api idle_loop reaper | crash-mode meeting reaches a terminal status (idle_loop swept the vanished bot)           |
| MEETING_HAS_RECORDING                   | recording lifecycle         | meeting.data.recordings array is non-empty                                                 |
| FINALIZE_MARKER_IS_SERVER_SIDE_MASTER   | Pack U.5                    | recordings[0].finalize == "recording_finalizer.master" (proves server-side path took)     |
| STORAGE_PATH_AT_MASTER                  | Pack U                      | storage_path ends at /audio/master.{webm\|wav}                                            |
| DOWNLOAD_URL_POINTS_AT_MASTER           | Pack D-3                    | /download presigned URL path ends at /audio/master.*                                       |
| MASTER_SIZE_PLAUSIBLE                   | Pack U / Pack M regression  | content-length ≥ 100KB (catches the ~270KB Pack-M-fragment regression class)              |
| MASTER_DURATION_PLAUSIBLE               | Pack U                      | ffprobe duration ≥ 0.5 × `--duration` and ≥ 30s                                           |
| SEGMENT_FITS_AUDIO_TIMELINE             | Pack U unified-alignment    | last segment.end_time ≤ master_duration + 5s tolerance                                     |
| NO_HALLUCINATION_PHRASES                | Pack FM-274                 | transcript contains no canonical hallucinations ("thanks for watching", etc.)             |
| BOT_LOGS_FIELD_PRESENT                  | Pack O                      | meeting.data.bot_logs JSONB present (skip if Pack O off in this deployment)               |
| BOT_RESOURCES_FIELD_PRESENT             | Pack T                      | meeting.data.bot_resources JSONB present (skip if Pack T off)                             |
| CHUNK_RATE_PLAUSIBLE                    | recording-incremental       | chunks/min in [0.5, 30] — catches both no-chunks and runaway chunk-emission regressions   |
| BOT_MEMORY_BOUNDED                      | Pack M chunk-leak           | linear fit slope < 5 MB/min over RSS samples — catches the v0.10.5 chunk-accumulation leak |
| HALLUCINATION_CORPUS_IN_IMAGE           | Pack FM-274                 | docker exec ls /app/.../hallucinations/ shows en/es/pt/ru.txt                             |
| DASHBOARD_NO_DUPLICATE_MEETINGS         | issue #304                  | paginated /meetings has no duplicate ids across pages                                       |

## What this harness does NOT cover

Static checks (registry: BROWSER_UTILS_INJECTED_BEFORE_PIPELINE_START,
SHARED_AUDIO_PIPELINE_MODULE_EXISTS, hallucination-corpus-gitignore-exception,
SDP-munge-site2-removed, etc.) are covered by `tests3/tests/v0.10.6-static-greps.sh`
and aren't real-meeting-driven.

Helm chart hardening (Pack H: maxUnavailable=0, replicaCount=2, --atomic) is
deployment-time, not meeting-time. Covered by `release-validate` against the
helm cell.

Webhook delivery is configurable per-user; if the test user has webhooks
disabled the harness won't see fires. Run `tests3/tests/webhooks.sh` separately
if that pack is in scope for the cycle.

## Operator guidance

- Use a non-trivial meeting (≥1 audible speaker for ≥30s) so transcription
  produces segments — without segments the SEGMENT_FITS_AUDIO_TIMELINE and
  NO_HALLUCINATION_PHRASES assertions skip.
- For Zoom, the URL must include `?pwd=` — Zoom rejects bots that don't
  send the passcode.
- For Teams, include `?p=` — same reason.
- For crash mode on helm, the LKE kubeconfig must be at
  `tests3/.state-helm/lke_kubeconfig`. Compose uses `docker kill` directly.
  Lite uses `ssh root@<vm_ip>`.
- If you don't see `ADMIT BOT NOW` for a cell, the cell's gateway/token
  resolution failed — check `tests3/.state-<deployment>/{gateway_url,api_token}`.
