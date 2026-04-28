---
services:
- meeting-api
- runtime-api
- vexa-bot
---

# Bot Lifecycle

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 90%**

## What

Meeting bots join Google Meet, Microsoft Teams, and Zoom (via Web Client),
transcribe audio, and leave. Each bot is a Docker container running
Playwright that navigates to a meeting URL, joins, captures audio, and
reports state changes back to meeting-api via HTTP callbacks.

**v0.10.5 Path 3 trust model.** Meeting URL inputs are accepted in three shapes:

1. `(platform + native_meeting_id)` — canonical
2. URL alone — server parses, extracts native_meeting_id from canonical Zoom/Meet/Teams URLs
3. `(meeting_url + platform)` — **white-label / enterprise URLs** (LFX, AWS Chime, Bloomberg-style portals). Server best-effort extracts a numeric ID where possible (regex `\b(\d{9,11})\b`), falls back to a hash sentinel for opaque URLs. Bot navigates the URL **verbatim** — does NOT rewrite to canonical Zoom Web Client. A human can VNC in to click through any portal-side T&C / consent / captcha page.

This means the schema accepts URLs we've never seen before. The cost: when the bot can't auto-navigate the portal, it escalates to `needs_human_help` and waits up to 5 min for human VNC assistance (vs the 30s wait for canonical URLs).

## State machine (v0.10.5)

```
    POST /bots
        │
        ▼
  ┌───────────┐
  │ requested  │  meeting record created, container spawning
  └─────┬─────┘
        │ bot callback: joining
        ▼
  ┌───────────┐
  │  joining   │  container running, navigating to meeting URL
  └──┬──┬─────┘     (5min selector wait for white-label URLs;
     │  │           30s wait for canonical platform URLs)
     │  │
     │  └── bot callback: needs_human_help ──► ┌──────────────────┐
     │                                         │ needs_human_help  │ ◄─── audio_join_failed
     │  bot callback: awaiting_admission       └────┬─────────────┘      escalates here too
     │                                              │ user resolves via VNC
     ▼                                              ▼
  ┌────────────────────┐                     back to active
  │ awaiting_admission  │  in lobby, waiting for host to admit
  └──────┬─────────────┘
         │ bot callback: active (host admitted)
         ▼
  ┌───────────┐
  │  active    │  in meeting; audio capture pipeline running
  └──┬──┬─────┘   (NB: `active` ≠ "actually capturing audio" — see Pack Σ
     │  │             section below; v0.10.6 splits this into
     │  │             in_call_not_recording / in_call_recording)
     │  │
     │  └── DELETE /bots (user) ─────────────┐
     │  └── scheduler timeout (max_bot_time) ─┤
     │  └── bot self-exit (any reason)        │
     │                                        ▼
     │                                  ┌───────────┐
     │                                  │ stopping   │  leave cmd sent, finalizing
     │                                  └─────┬─────┘
     │                                        │ bot exit callback OR
     │                                        │ stale-stopping sweep (5min)
     │                                        ▼
     │                       ┌─────── Pack J classifier ─────────┐
     │                       │ (services/meeting-api/             │
     │                       │  meeting_api/callbacks.py:52)      │
     │                       │                                    │
     │                       │ • LEFT_ALONE → completed          │
     │                       │ • STOPPED + reached_active +       │
     │                       │   has_segments → completed         │
     │                       │ • STOPPED + transcribe_disabled    │
     │                       │   OR <30s → completed              │
     │                       │ • STOPPED + reached_active + 0    │
     │                       │   segments → FAILED (no_audio)    │
     │                       │ • STOPPED + !reached_active →     │
     │                       │   FAILED (before_admission)       │
     │                       │ • EVICTED / TIMEOUT / etc → FAILED │
     │                       └────┬─────────────────────┬────────┘
     │                            ▼                     ▼
     │                       ┌──────────┐         ┌──────────┐
     │                       │ completed │         │  failed   │  terminal: failure_stage
     │                       └──────────┘         └──────────┘  + completion_reason
     │                                                  ▲
     └── error at any point ──────────────────────────┘
```

## Transition rules (v0.10.5)


| From               | To                 | Trigger                                              |
| ------------------ | ------------------ | ---------------------------------------------------- |
| requested          | joining            | Bot callback                                         |
| requested          | failed             | Validation error, spawn failure                      |
| requested          | stopping           | User DELETE                                          |
| joining            | awaiting_admission | Bot callback                                         |
| joining            | active             | Bot callback (no waiting room)                       |
| joining            | needs_human_help   | Bot escalation (auth_required, portal page stuck)    |
| joining            | failed             | Platform error                                       |
| awaiting_admission | active             | Bot callback (admitted)                              |
| awaiting_admission | needs_human_help   | Bot escalation                                       |
| awaiting_admission | failed             | Rejected, timeout                                    |
| needs_human_help   | active             | User resolved via VNC                                |
| needs_human_help   | failed             | User gave up OR escalation timeout                   |
| active             | needs_human_help   | audio_join_failed (Zoom Web 3-attempt loop fell through; v0.10.5 commit 37316d6) |
| active             | stopping           | User DELETE, scheduler timeout                       |
| active             | completed          | Bot self-exit, classifier verifies positive proof    |
| active             | failed             | Bot self-exit, classifier finds no_audio / no proof  |
| stopping           | completed          | Pack J classifier rules (see below)                  |
| stopping           | failed             | Pack J classifier rules (see below)                  |
| stopping           | failed (sweep)     | Stale > 5min, force-finalize via Pack E.3.2 sweep    |


## Pack J classifier (v0.10.5 — the routing principle)

**Status used to default to `completed` on any clean exit.** [PLATFORM] data on issue #255 showed 47% (557/1183) of `completed` meetings in 30 days had zero transcripts. The bot was reporting eviction / admission_timeout / max_bot_time correctly via `completion_reason`, but the meeting-api callback handler ignored those signals.

`_classify_stopped_exit` (services/meeting-api/meeting_api/callbacks.py:52) is the canonical routing function. **Every code path that transitions to a terminal state goes through it.** If you find a write site that doesn't, that's a bug — every silent-classification fix in v0.10.5 was the same pattern (Pack X session caught 4 bypass sites: status_change, exit_callback, DELETE no-container, DELETE fast-path).

The principle:

> `completed` = **positive proof of success**. `failed` = absence of that proof OR explicit failure signal.

Concretely, in priority order:

1. `completion_reason ∈ {AWAITING_ADMISSION_TIMEOUT, AWAITING_ADMISSION_REJECTED, EVICTED, MAX_BOT_TIME_EXCEEDED, VALIDATION_ERROR, STOPPED_BEFORE_ADMISSION, STOPPED_WITH_NO_AUDIO}` → **failed** with that reason
2. `LEFT_ALONE` → **completed** (everyone else left, bot did its job)
3. `STOPPED` with `not reached_active` → **failed (STOPPED_BEFORE_ADMISSION)**
4. `STOPPED` with `reached_active AND (duration < 30s OR transcribe_disabled)` → **completed** (user-initiated quick stop)
5. `STOPPED` with `reached_active AND duration ≥ 30s AND transcribe_enabled AND segment_count == 0` → **failed (STOPPED_WITH_NO_AUDIO)**
6. `STOPPED` with `reached_active AND segment_count > 0` → **completed**
7. unknown reason → **failed** (defensive — don't silently green-light)

## Escalation (needs_human_help)

When a bot is stuck in an unknown state, it triggers escalation:

1. Bot-side: `callNeedsHumanHelpCallback(botConfig, reason)` → status change to `needs_human_help`
2. Meeting-api: stores `meeting.data.escalation = {reason, escalated_at, session_token, vnc_url}`
3. Meeting-api: registers container in Redis for gateway VNC proxy (`browser_session:{meeting_id}`)
4. Dashboard: receives status change via WS, shows "Bot needs help" panel with VNC link + Stop button
5. User: connects via VNC at `/b/{meeting_id}/vnc/`, manually resolves the issue
6. Resolution: bot detects admission → status changes to `active`. Or user gives up → `failed`.

Triggers (v0.10.5):

- **Admission flow stuck** — bot can't determine if it's in lobby, admitted, or rejected (Google Meet, Teams, Zoom)
- **`audio_join_failed`** (v0.10.5 commit 37316d6) — Zoom Web 3-attempt audio-join loop falls through. Without computer audio, no `<audio>` elements are created and per-speaker capture gets zero data. Previously this was a silent failure ("active" status, 0 transcripts forever). Now escalates so VNC link surfaces.
- **Portal page stuck** (Path 3) — bot navigates a white-label URL (LFX, AWS Chime, Bloomberg) and can't find Zoom's pre-join name input within 5 min. User VNCs in to click through portal page.

Implemented for: Google Meet, Teams, Zoom admission flows; Zoom Web audio-join.

## Completion reasons (v0.10.5 enum)


| Reason                        | Routes to | Trigger                               |
| ----------------------------- | --------- | ------------------------------------- |
| `stopped`                     | classifier | User called DELETE /bots             |
| `awaiting_admission_timeout`  | failed    | Waited > max_wait_for_admission       |
| `awaiting_admission_rejected` | failed    | Host rejected bot from lobby          |
| `left_alone`                  | completed | No participants > max_time_left_alone |
| `evicted`                     | failed    | Host removed bot from meeting         |
| `max_bot_time_exceeded`       | failed    | Scheduler timeout fired               |
| `validation_error`            | failed    | Request validation failed             |
| `stopped_before_admission`    | failed    | **v0.10.5 Pack J** — DELETE before reaching active (~432 cases / 30d in prod data) |
| `stopped_with_no_audio`       | failed    | **v0.10.5 Pack J** — bot ran ≥30s with transcribe enabled, produced 0 segments (~125 cases / 30d) |


## Failure stages


| Stage                | When                                           |
| -------------------- | ---------------------------------------------- |
| `requested`          | Pre-spawn validation fails                     |
| `joining`            | Platform join error (wrong URL, auth, network) |
| `awaiting_admission` | Waiting room error                             |
| `active`             | Runtime crash after admission                  |

> **v0.10.5 caveat:** when the bot's exit callback omits `failure_stage`, meeting-api defaults it to `ACTIVE` (`callbacks.py:312`). This means a bot that crashed in `joining` looks like it failed in `active` if its exit callback was thin. Pack Σ (v0.10.6) replaces the default with `unknown` so the field always reflects observed truth.


## Lifetime management

Meeting bots use **model 1** (consumer-managed) from runtime-api. `idle_timeout: 0` — runtime-api never auto-stops them. Meeting-api owns the full lifecycle through four mechanisms:

### 1. Server-side kill switch: scheduler job (max_bot_time)

When a bot is created, meeting-api schedules a deferred HTTP job in runtime-api's scheduler:

- `execute_at = now + max_bot_time` (default 2h)
- Job: `DELETE /bots/internal/timeout/{meeting_id}`
- When fired: sets `pending_completion_reason=MAX_BOT_TIME_EXCEEDED`, transitions to stopping
- Job cancelled when meeting reaches terminal state (completed/failed)

This is the **hard limit**. No meeting bot can run longer than `max_bot_time`.

### 2. Bot-side timers (client-enforced)

The bot process runs timers internally. When triggered, bot self-exits with a specific reason:


| Timer                    | Default      | What happens                                   |
| ------------------------ | ------------ | ---------------------------------------------- |
| `no_one_joined_timeout`  | 120s (2min)  | Nobody joined after bot entered meeting → exit |
| `max_wait_for_admission` | 900s (15min) | Stuck in lobby → exit                          |
| `max_time_left_alone`    | 900s (15min) | All participants left → exit                   |


Bot exit → Docker "die" event → runtime-api `on_exit` → meeting-api exit callback → status updated.

### 3. User DELETE

`DELETE /bots/{platform}/{native_id}` → Redis `{"action": "leave"}` → bot exits → completed.

### 4. Platform events

Bot detects: evicted by host, meeting ended, connection lost → self-exit with appropriate reason.

### Timeout configuration

Resolution order: per-request `automatic_leave` → `user.data.bot_config` → system defaults.


| Timeout                  | Default           | Enforced by            | Configurable          |
| ------------------------ | ----------------- | ---------------------- | --------------------- |
| `max_bot_time`           | 7,200,000ms (2h)  | Scheduler job (server) | per-request, per-user |
| `max_wait_for_admission` | 900,000ms (15min) | Bot timer (client)     | per-request, per-user |
| `max_time_left_alone`    | 900,000ms (15min) | Bot timer (client)     | per-request, per-user |
| `no_one_joined_timeout`  | 120,000ms (2min)  | Bot timer (client)     | per-request, per-user |


### Contrast with other container types


|                          | Meeting bot                               | Browser session                          | Agent               |
| ------------------------ | ----------------------------------------- | ---------------------------------------- | ------------------- |
| Who manages lifetime     | meeting-api                               | gateway (planned)                        | agent-api           |
| Server-side kill         | scheduler job (max_bot_time)              | idle_timeout (planned)                   | idle_timeout (300s) |
| Client-side kill         | bot timers (alone, admission, join)       | none                                     | none                |
| Heartbeat                | none needed (scheduler is the safety net) | gateway /touch on /b/* traffic (planned) | agent-api /touch    |
| runtime-api idle_timeout | 0 (disabled)                              | >0 (planned)                             | 300s                |


## Delayed stop mechanism (v0.10.5: Pack D.2 durable outbox)

When user calls DELETE /bots or scheduler fires:

1. Send Redis command `{"action": "leave"}` to bot
2. Transition to `stopping`
3. **Enqueue stop in Redis Stream outbox** (`container_stop_outbox.py`) — `fire_at = now + 90s`
4. Pack D.2 outbox consumer (in `sweeps.py`) fires the stop call to runtime-api at `fire_at`
5. If bot exits naturally within 90s → exit callback fires → Pack J classifier → terminal state
6. If 90s expires → outbox consumer calls `DELETE /containers/{name}` on runtime-api
7. If outbox call fails → retries with backoff up to 5x → DLQ on exhaustion

**Why Pack D.2 outbox** (vs the v0.10.4 in-memory `asyncio.create_task`): the in-memory task was lost on meeting-api restart. Bot would stay running, meeting stuck in `stopping`. Outbox is durable across restarts and retries deterministically.

**Stale `bot_container_id` detection** (v0.10.5 commit e16fa7e): at DELETE time, before scheduling the outbox stop, meeting-api validates the resolved `container_name` against runtime-api's `GET /containers/{name}`. If 404 or `status != running`, the container is already gone — code routes through the **no-container Pack J branch** for synchronous finalization instead of waiting for the 5-min stale-stopping sweep. Closes the "stuck in stopping after stack restart" UX gap.

**Stale-stopping sweep** (v0.10.5 Pack E.3.2, `sweeps.py:_sweep_stale_stopping`): every 60s, scans for rows in `stopping` for >5min, force-finalizes via Pack J classifier. Safety net for any bot whose exit callback never landed (e.g. container OOM-killed without graceful exit).

Browser sessions: delay = 0s (no meeting to leave).

## Concurrency

Users have a `max_concurrent_bots` limit. The concurrency check counts meetings in non-terminal states: `requested`, `joining`, `awaiting_admission`, `active`.

`stopping` is NOT counted. When user calls DELETE:

1. Status → stopping → concurrency slot released immediately
2. Container still running (up to 90s delayed stop)
3. User can create a new bot right away

This is by design — user shouldn't wait 90s for the slot. Two containers may run simultaneously briefly, but only one "active" meeting counts against the limit.

Browser sessions are included in the concurrency count (same query). They release the slot on stop (delay=0, immediate).

## Callbacks (bot → meeting-api)


| Endpoint                                     | Called when                    |
| -------------------------------------------- | ------------------------------ |
| `/bots/internal/callback/status_change`      | Any state transition (unified) |
| `/bots/internal/callback/exited`             | Bot process exits              |
| `/bots/internal/callback/joining`            | Bot navigating to meeting      |
| `/bots/internal/callback/awaiting_admission` | Bot in lobby                   |
| `/bots/internal/callback/started`            | Bot admitted, active           |
| `/bots/internal/test/session-bootstrap`      | **Pack X** synthetic test rig — creates a MeetingSession row directly. Gated by `VEXA_ENV != "production"`; returns 404 in production. |


All callbacks: 3 retries, exponential backoff (1s, 2s, 4s), 5s timeout per attempt.

Status transitions are protected by `SELECT FOR UPDATE` (row-level lock) to prevent TOCTOU races.

**v0.10.5 — `dry_run` flag.** POST /bots accepts `dry_run: true` (gated by `VEXA_ENV != "production"`). When set, meeting-api creates the meeting row + bot_config but skips `_spawn_via_runtime_api`. Used by Pack X tests to exercise the schema/validation/Path-3-extraction layers without spending compute on a real bot.

## Pack X synthetic test rig (v0.10.5)

Pack J's `status_change` coverage gap shipped through the entire pre-existing validate matrix because the matrix didn't drive a real meeting end-to-end — static checks, smoke matrix, unit tests, reproducer tests all reported green. The bug surfaced only when a human ran a real Zoom bot.

Pack X (`tests3/synthetic/`) closes this with a deterministic, no-external-dependency way to drive the OSS-side meeting lifecycle:

- `tests3/synthetic/rig.sh` — bash+curl primitives: `rig_get_user_token`, `rig_spawn_dryrun`, `rig_session_bootstrap`, `rig_callback`, `rig_delete_bot`, `rig_get_state`, `rig_assert_state`, `rig_setup_meeting`, `rig_drive_to_active`, `rig_parallel`, `rig_assert_log`, `rig_baseline_redis_keys`, `rig_assert_no_redis_leak`
- 9 scenarios covering Pack J status_change bypass, exit-callback path, callback ordering races, DELETE no-container, FAILED-branch completion_reason persistence, terminal idempotency, redis-key leakage, fuzz callback payloads, stale failure_stage tolerance.

This is the leverage tool that caught **5 additional bugs** during v0.10.5 develop iterations that the pre-existing matrix had silently passed.

**Pack Y groom seed (v0.10.6)** extends Pack X from path-enumeration to invariant-based property fuzz — see scope.yaml tail.

## Components


| Component                   | File                                                            | Role                                                              |
| --------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------- |
| Bot creation                | `services/meeting-api/meeting_api/meetings.py:870-1130`         | POST /bots, Path 3 extraction, spawn container, dry_run gate      |
| Status callbacks            | `services/meeting-api/meeting_api/callbacks.py`                 | Bot → meeting-api state updates; Pack J classifier                |
| Pack J classifier           | `services/meeting-api/meeting_api/callbacks.py:52`              | `_classify_stopped_exit` — single source of truth for completed/failed routing |
| Stop/timeout                | `services/meeting-api/meeting_api/meetings.py:1530-1650`        | DELETE /bots, stale-container detection, fast-path, no-container Pack J branch |
| Container-stop outbox       | `services/meeting-api/meeting_api/container_stop_outbox.py`     | Pack D.2 — durable Redis Stream outbox for DELETE container-stop  |
| Sweeps                      | `services/meeting-api/meeting_api/sweeps.py`                    | Pack E.3.2 stale-stopping + Pack D.2 outbox consumer + Pack H aggregation-failure retry |
| Bot core                    | `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts`    | Join, admit, capture flow                                         |
| Zoom Web join               | `services/vexa-bot/core/src/platforms/zoom/web/join.ts`         | URL parser (canonical rewrite vs white-label passthrough); 5min wait for portals |
| Zoom Web prepare            | `services/vexa-bot/core/src/platforms/zoom/web/prepare.ts`      | Audio-join 3-attempt loop + needs_human_help escalation on failure |
| Unified callback            | `services/vexa-bot/core/src/services/unified-callback.ts`       | Bot → API state reporting                                         |
| Bot exit-reason callbacks   | `services/vexa-bot/core/src/utils.ts`                           | `callJoiningCallback`, `callStartupCallback`, `callNeedsHumanHelpCallback` |
| Scheduler                   | `services/runtime-api/runtime_api/scheduler.py`                 | max_bot_time enforcement                                          |
| Runtime exit callback       | `services/runtime-api/runtime_api/lifecycle.py`                 | Pod-event capture (OOMKill, Evicted) + durable callback delivery  |
| Pack X synthetic rig        | `tests3/synthetic/rig.sh` + `tests3/synthetic/scenarios/*.sh`   | Entropy testing — drives the lifecycle without external deps      |


## DoD


<!-- BEGIN AUTO-DOD -->
<!-- Auto-written by tests3/lib/aggregate.py from release tag `0.10.0-260427-1802`. Do not edit by hand — edit the sidecar `dods.yaml` + re-run `make -C tests3 report --write-features`. -->

**Confidence: 91%** (gate: 90%, status: ✅ pass)

| # | Behavior | Weight | Status | Evidence (modes) |
|---|----------|-------:|:------:|------------------|
| create-ok | POST /bots spawns a bot container and returns a bot id | 15 | ✅ pass | `helm`: containers/create: bot 1 created |
| create-alive | Bot process is running 10s after creation (not crash-looping) | 15 | ✅ pass | `helm`: containers/alive: bot process running after 10s |
| bots-status-not-422 | GET /bots/status never returns 422 (schema stable under concurrent writes) | 5 | ❌ fail | `lite`: smoke-contract/BOTS_STATUS_NOT_422: GET /bots/status returns 200 — no route collision with /bots/{meeting_id}; `compose`: smoke-contract/BOTS_STATUS_NOT_422: GET /bots/status returns 200 — no route collision with /bots/{meeting_id}; `helm`: smoke-contract/BOTS_STATUS_NOT_422: HTTP 401 (ex… |
| removal | Container fully removed after DELETE /bots/... | 10 | ✅ pass | `helm`: containers/removal: container fully removed after stop |
| status-completed | Meeting.status=completed after stop (not failed/stuck) | 10 | ✅ pass | `helm`: containers/status_completed: meeting.status=completed after stop (waited 1x5s) |
| graceful-leave | Bot leaves the meeting gracefully on stop (no force-kill by default) | 5 | ✅ pass | `lite`: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping treated as completed, not failed; `compose`: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping treated as completed, not failed; `helm`: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping trea… |
| route-collision | No Starlette route collisions — /bots/{id} and /bots/{platform}/{native_id} do not clash | 5 | ✅ pass | `lite`: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, not /bots/{id} which collides with /bots/status; `compose`: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, not /bots/{id} which collides with /bots/status; `helm`: smoke-static/ROUTE_COLLISION: bot detail r… |
| timeout-stop | Bot auto-stops after automatic_leave timeout (no_one_joined_timeout) | 10 | ⚠️ skip | `helm`: containers/timeout_stop: bot still running after 60s (timeout may count from lobby) |
| concurrency-slot | Concurrent-bot slot released immediately on stop — next create succeeds | 10 | ✅ pass | `helm`: containers/concurrency_slot: slot released, B created (HTTP 201) |
| no-orphans | No zombie/exited bot containers left after a lifecycle run | 10 | ✅ pass | `helm`: containers/no_orphans: no exited/zombie containers |
| status-webhooks-fire | Status-change webhooks fire for every transition when enabled in webhook_events | 5 | ✅ pass | `helm`: webhooks/e2e_status: 1 status-change webhook(s) fired: meeting.status_change |
| recording-incremental-chunk-upload | bot uploads each MediaRecorder chunk as it arrives; meeting-api accepts chunk_seq on /internal/recordings/upload | 15 | ✅ pass | `lite`: smoke-static/RECORDING_UPLOAD_SUPPORTS_CHUNK_SEQ: /internal/recordings/upload accepts chunk_seq: int form parameter; `compose`: smoke-static/RECORDING_UPLOAD_SUPPORTS_CHUNK_SEQ: /internal/recordings/upload accepts chunk_seq: int form parameter |
| bot-records-incrementally | bot recording.ts calls MediaRecorder.start with ≥15s timeslice AND uploads each chunk via __vexaSaveRecordingChunk | 10 | ✅ pass | `lite`: bot-records-incrementally/BOT_RECORDS_INCREMENTALLY: bot recording.ts wires ≥15s MediaRecorder timeslice + __vexaSaveRecordingChunk; `compose`: bot-records-incrementally/BOT_RECORDS_INCREMENTALLY: bot recording.ts wires ≥15s MediaRecorder timeslice + __vexaSaveRecordingChunk |
| recording-survives-mid-meeting-kill | SIGKILL mid-recording leaves already-uploaded chunks durable in MinIO; Recording.status stays IN_PROGRESS until is_final=true | 10 | ✅ pass | `compose`: recording-survives-sigkill/RECORDING_SURVIVES_MID_MEETING_KILL: chunk_seq contract verified statically (see RECORDING_UPLOAD_SUPPORTS_CHUNK_SEQ) |
| runtime-api-stop-grace-matches-pod-spec | runtime-api delete_namespaced_pod grace_period_seconds matches the pod spec terminationGracePeriodSeconds | 5 | ✅ pass | `helm`: smoke-static/RUNTIME_API_STOP_GRACE_MATCHES_POD_SPEC: runtime-api kubernetes.py stop() passes its `timeout` parameter through as grace_period_seconds on pod deletion — bot graceful-leave has the full grace window. Current default: 60s (matches pod spec's terminationGracePeriodSeconds=60). |
| runtime-api-exit-callback-durable | runtime-api exit callback delivery is durable across consumer outages (idle_loop re-sweeps pending records) | 10 | ✅ pass | `compose`: runtime-api-exit-callback-durable/RUNTIME_API_EXIT_CALLBACK_DURABLE: durable-delivery contract covered by idle_loop_sweeps + no_delete_on_exhaustion static checks above |
| runtime-api-idle-loop-sweeps-pending-callbacks | services/runtime-api lifecycle.py idle_loop iterates pending callbacks each tick and retries delivery | 5 | ✅ pass | `lite`: smoke-static/RUNTIME_API_IDLE_LOOP_SWEEPS_PENDING_CALLBACKS: runtime-api idle_loop references list_pending_callbacks — the durable-delivery sweep is wired; `compose`: smoke-static/RUNTIME_API_IDLE_LOOP_SWEEPS_PENDING_CALLBACKS: runtime-api idle_loop references list_pending_callbacks — the… |
| bot-video-default-off | POST /bots `video` field defaults to False — video recording is opt-in, not opt-out | 5 | ✅ pass | `lite`: smoke-static/BOT_VIDEO_DEFAULT_OFF: POST /bots `video` field defaults to False — video recording is opt-in; audio-only is the default for transcription-focused deployments; `compose`: smoke-static/BOT_VIDEO_DEFAULT_OFF: POST /bots `video` field defaults to False — video recording is opt-i… |

<!-- END AUTO-DOD -->

## Failure modes


| Symptom                                         | Cause                                                  | Fix                                                              | Learned                                         |
| ----------------------------------------------- | ------------------------------------------------------ | ---------------------------------------------------------------- | ----------------------------------------------- |
| Bot shows "failed" after successful meeting     | exit_code=1 on self_initiated_leave treated as failure | callbacks.py: exit during stopping → completed                   | Graceful leave ≠ crash                          |
| **47% of completed meetings had 0 transcripts** (#255 prod data) | callback handler defaulted any clean exit to status=completed regardless of `completion_reason` signal | **v0.10.5 Pack J classifier** — `_classify_stopped_exit` routes by completion_reason + reached_active + segment_count. Two new reasons: `STOPPED_WITH_NO_AUDIO` (125 cases / 30d) + `STOPPED_BEFORE_ADMISSION` (432 cases / 30d) | `failed` is the safe default — silent success kills observability |
| Bot at status=active forever, 0 transcripts (Zoom Web) | Zoom Web `prepareZoomWebMeeting` 3-attempt audio-join loop fell through silently. Without computer audio, no `<audio>` elements created, per-speaker capture bails after 10×2s retry. Bot reports `active` but produces nothing. | **v0.10.5 commit 37316d6** — escalate `audio_join_failed` to `needs_human_help`. Dashboard shows VNC link; user clicks "Join with Computer Audio" manually; bot picks up. | `bot.active` ≠ `bot.actually_capturing_audio` — closed by Pack Σ in/recording state split |
| LFX/AWS/enterprise Zoom URL rejected by parser | Bot `buildZoomWebClientUrl` only matched canonical `/j/<id>` path; LFX URLs (`/meeting/<id>`) crashed with "Cannot extract meeting ID". | **v0.10.5 commit da97506** — canonical `*.zoom.us` URLs rewrite to web client; white-label URLs pass through verbatim with 5min selector wait so human can VNC in to navigate portal page. | Per-vendor parsers are an arms race we lose; trust the user-supplied platform pick (Path 3) |
| Meeting stuck in `stopping` after stack restart | DELETE flow read `meeting.bot_container_id` (truthy) and skipped no-container Pack J branch, even though the container had been wiped by a redeploy. Pack D.2 outbox stop succeeded (`Process not in registry`) but no terminal transition fired. Pack E.3.2 sweep finally reaped at 5min. | **v0.10.5 commit e16fa7e** — DELETE flow validates `bot_container_id` via `GET /containers/{name}`. If 404 or status≠running, null out container_name → no-container Pack J branch fires → synchronous Pack J finalize. | Sweep is the safety net, not the first line of defense |
| `failure_stage: active` paints over reality    | When bot exit callback omits `failure_stage`, meeting-api defaults to `MeetingFailureStage.ACTIVE` (`callbacks.py:312`). A bot that crashed in `joining` (selector timeout pre-`callJoiningCallback`) looks like it failed in `active`. | **Pack Σ (v0.10.6)** — replace default with explicit `unknown`. Field always reflects observed truth, never default-painting. | Stacked defaults compound (bot's `self_initiated_leave` default + server's `ACTIVE` default = misleading user-facing message) |
| Bot stuck on name input (unauthenticated GMeet) | No saved cookies, Google shows "Your name" prompt      | Bot should fill name or fail fast                                | Open bug                                        |
| Recording upload fails on lite (host network)   | MinIO endpoint `http://minio:9000` — DNS unresolvable in host-network mode. Bot uploads to `/internal/recordings/upload`, meeting-api then uploads to MinIO. meeting-api can't resolve `minio`. | Set `MINIO_ENDPOINT=http://localhost:9000` for lite. | `recordings.py:194`, `storage.py:90`. |
| MinIO retry blocks bot completion (lite)         | **Lite**: No MinIO running. `recording_enabled` defaults to `True` (`meetings.py:796`). Every bot stop: graceful leave → upload to `minio:9000` → DNS fail → botocore retries 4x (~30s) → 500 → then bot callback fires → completed. Total stop time ~32s instead of ~6s. | Set `RECORDING_ENABLED=false` in lite .env when no MinIO, or run MinIO alongside lite. | `meetings.py:796`, `recordings.py:194`, `storage.py:90`. |
| K8s exit callback not fired from DELETE endpoint | `DELETE /containers/{name}` (`runtime-api/api.py:308-318`) calls `state.set_stopped()` but NOT `_fire_exit_callback()`. Exit callback only fires from the pod watcher. If watcher misses event (reconnect gap, double-delete), callback never fires. | Fix: fire exit callback from DELETE endpoint. | `api.py:308-318`, `lifecycle.py:65-110`. |
| Redis client stays None after startup failure   | `meeting-api/main.py:101-108`: if Redis down at startup, `redis_client=None` forever. No reconnect. All Redis ops silently skipped — no PubSub, no segment reads from Redis, no stream consumption. | Restart meeting-api after Redis recovers. Fix: add reconnect logic. | `main.py:101-113`. |
| Auto-admit clicks text node instead of button   | `text=/Admit/i` matched non-clickable element          | Multi-phase CDP: panel → expand → `button[aria-label^="Admit "]` | Always use element-type + aria-label for clicks |
| "Waiting to join" section collapsed             | Google Meet collapses lobby list after ~10s            | Expand before looking for admit button                           | Check visibility before assuming DOM state      |


## Future direction (v0.10.6 Pack Σ — meeting lifecycle taxonomy redesign)

Documented as a groom seed in `tests3/releases/260427/scope.yaml`. Carries forward as the headline pack for v0.10.6.

**Trigger:** v0.10.5 retrospective + recall.ai documentation study (2026-04-28). Three concerns conflate in the current binary `completed | failed`:

- Users see binary status with no plain-language cause and no action affordance
- Engineering can't isolate bug rate from user/host/environment-driven failures
- Product can't read funnel cause attribution

Pack J's "default to failed when uncertain" is engineering-defensive but user-hostile and product-hostile. v0.10.5 closed the silent-success class; Pack Σ closes the silent-classification class entirely.

### Layer 1: 9 lifecycle states (recall.ai-aligned)

| Pack Σ state            | Maps to recall.ai event       | Notes                                                        |
| ----------------------- | ----------------------------- | ------------------------------------------------------------ |
| `requested`             | (no equivalent)               | vexa-specific pre-spawn state                                |
| `joining`               | `bot.joining_call`            | direct                                                       |
| `in_waiting_room`       | `bot.in_waiting_room`         | renamed from `awaiting_admission`                            |
| `in_call_not_recording` | `bot.in_call_not_recording`   | **NEW** — split from `active`; bot in meeting DOM, audio NOT yet confirmed |
| `in_call_recording`     | `bot.in_call_recording`       | **NEW** — split from `active`; audio confirmed, segments flowing |
| `needs_human_help`      | (no equivalent)               | vexa-specific VNC affordance                                 |
| `call_ended`            | `bot.call_ended`              | renamed from `stopping`                                      |
| `done`                  | `bot.done`                    | renamed from `completed`                                     |
| `fatal`                 | `bot.fatal`                   | renamed from `failed`                                        |

The done/fatal cut becomes one mechanical bit: **`done` if the meeting reached `in_call_recording` at any point, `fatal` otherwise.** This matches recall.ai's billing semantic (they don't bill for `bot.fatal`).

7-of-9 states map directly to recall events → a future `recall_to_vexa` API translator becomes a static lookup table, not business logic.

### Layer 2: `meeting.data.cause` field (~70 named values, agency-prefixed)

Single source of truth for terminal-state diagnosis. Computed by **one classifier function** called from every write site (replacing today's scattered Pack J calls). Agency prefixes:

| prefix         | meaning                                | engineering query                                     |
| -------------- | -------------------------------------- | ----------------------------------------------------- |
| `user_*`       | API caller's action                    | product funnel analysis                               |
| `host_*`       | meeting host's action                  | environment / sales-side education                    |
| `platform_*`   | platform's hard rule                   | environment / docs                                    |
| `zoom_*` / `google_meet_*` / `teams_*` / `webex_*` | platform-specific failures | environment, per-platform |
| `bot_self_*`   | bot's own configured leave             | normal end-of-meeting                                 |
| `bot_*`        | **OUR SOFTWARE FAULT**                 | **engineering bug rate** (filter by this prefix)      |
| `ux_gap_*`     | automation incomplete, escalation unresolved | product + engineering (new automation surface)  |
| `unknown`      | classifier rule didn't fire (bug)      | engineering must close coverage gap                   |

The principle: **every terminal meeting gets a specific cause, including the "we don't know" case as `unknown`** — never user-facing directly, but visible to engineering as a coverage gap.

### What this closes

5 parallel-surface-drift patterns documented in the v0.10.5 architectural-debt audit, each of which recurred as a bug during develop iterations:

1. `bot.active ≠ bot.actually_capturing_audio` → fixed by audio-confirmation gate at `in_call_not_recording → in_call_recording`
2. `failure_stage` defaulting to `ACTIVE` (callbacks.py:312) → fixed by `unknown` cause
3. `self_initiated_leave` defaulting in bot.ts:634 → fixed by central classifier producing cause from observed signals, not bot's reason string
4. `status='completed'` survived Pack J in DELETE-no-container, DELETE-fast-path, FAILED-branch → fixed by single canonical write path
5. Stuck-in-stopping (lite m30 2026-04-27) — sweep as first line of defense → already partially fixed in commit e16fa7e; full consolidation in Pack Σ

### What this doesn't change

- Bot platform code (browser automation stays the same)
- Helm chart / runtime-api lifecycle
- The two-month internal lifecycle invariants this README has documented since v0.10.4

Estimated ~5 days dev across meeting-api / dashboard / docs in v0.10.6.


