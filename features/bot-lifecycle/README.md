---

## services: [meeting-api, runtime-api, vexa-bot]
tests3:
  targets: [containers, bot, smoke]
  checks: [GRACEFUL_LEAVE, ROUTE_COLLISION, BOTS_STATUS_NOT_422]

# Bot Lifecycle

## What

Meeting bots join Google Meet / Teams, transcribe audio, and leave. Each bot is a Docker container running Playwright that navigates to a meeting URL, joins, captures audio, and reports state changes back to meeting-api via HTTP callbacks.

## State machine

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
  └──┬──┬─────┘
     │  │
     │  └── bot callback: needs_human_help ──► ┌──────────────────┐
     │                                         │ needs_human_help  │
     │  bot callback: awaiting_admission       └────┬─────────────┘
     │                                              │ user resolves via VNC
     ▼                                              ▼
  ┌────────────────────┐                     back to active
  │ awaiting_admission  │  in lobby, waiting for host to admit
  └──────┬─────────────┘
         │ bot callback: active (host admitted)
         ▼
  ┌───────────┐
  │  active    │  in meeting, capturing audio, transcribing
  └──┬──┬─────┘
     │  │
     │  └── DELETE /bots (user) ─────────────┐
     │  └── scheduler timeout (max_bot_time) ─┤
     │  └── bot self-exit (evicted, alone)    │
     │                                        ▼
     │                                  ┌───────────┐
     │                                  │ stopping   │  leave cmd sent, uploading recording
     │                                  └─────┬─────┘
     │                                        │ bot exit callback (exit_code)
     │                                        ▼
     │                                  ┌───────────┐
     │                                  │ completed  │  terminal: end_time set, container removed
     │                                  └───────────┘
     │
     └── error at any point ──────────► ┌───────────┐
                                        │  failed    │  terminal: failure_stage + error_details
                                        └───────────┘
```

## Transition rules


| From               | To                 | Trigger                                              |
| ------------------ | ------------------ | ---------------------------------------------------- |
| requested          | joining            | Bot callback                                         |
| requested          | failed             | Validation error, spawn failure                      |
| requested          | stopping           | User DELETE                                          |
| joining            | awaiting_admission | Bot callback                                         |
| joining            | active             | Bot callback (no waiting room)                       |
| joining            | needs_human_help   | Bot escalation                                       |
| joining            | failed             | Platform error                                       |
| awaiting_admission | active             | Bot callback (admitted)                              |
| awaiting_admission | needs_human_help   | Bot escalation                                       |
| awaiting_admission | failed             | Rejected, timeout                                    |
| needs_human_help   | active             | User resolved via VNC                                |
| needs_human_help   | failed             | User gave up                                         |
| active             | stopping           | User DELETE, scheduler timeout                       |
| active             | completed          | Bot self-exit (evicted, alone)                       |
| active             | failed             | Crash, disconnect                                    |
| stopping           | completed          | Bot exit (any exit code during stopping = completed) |
| stopping           | failed             | Bot exit with error before stop processed            |


## Escalation (needs_human_help)

When a bot is stuck in an unknown state during admission (not clearly in lobby, not admitted, not rejected), it triggers escalation:

1. Bot-side: `triggerEscalation(botConfig, reason)` → calls status change callback with `needs_human_help`
2. Meeting-api: stores `meeting.data.escalation = {reason, escalated_at, session_token, vnc_url}`
3. Meeting-api: registers container in Redis for gateway VNC proxy (`browser_session:{meeting_id}`)
4. Dashboard: receives status change via WS, can show VNC link to user
5. User: connects via VNC at `/b/{meeting_id}/vnc/`, manually resolves the issue
6. Resolution: bot detects admission → status changes to `active`. Or user gives up → `failed`.

Implemented for: Google Meet, Teams, Zoom admission flows.

## Completion reasons


| Reason                        | Trigger                               |
| ----------------------------- | ------------------------------------- |
| `stopped`                     | User called DELETE /bots              |
| `awaiting_admission_timeout`  | Waited > max_wait_for_admission       |
| `awaiting_admission_rejected` | Host rejected bot from lobby          |
| `left_alone`                  | No participants > max_time_left_alone |
| `evicted`                     | Host removed bot from meeting         |
| `max_bot_time_exceeded`       | Scheduler timeout fired               |
| `validation_error`            | Request validation failed             |


## Failure stages


| Stage                | When                                           |
| -------------------- | ---------------------------------------------- |
| `requested`          | Pre-spawn validation fails                     |
| `joining`            | Platform join error (wrong URL, auth, network) |
| `awaiting_admission` | Waiting room error                             |
| `active`             | Runtime crash after admission                  |


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


## Delayed stop mechanism

When user calls DELETE /bots or scheduler fires:

1. Send Redis command `{"action": "leave"}` to bot
2. Transition to `stopping`
3. Schedule `_delayed_container_stop(container_name, meeting_id, delay=90s)`
4. If bot exits naturally within 90s → exit callback fires → completed
5. If 90s expires → force stop container → safety finalizer sets completed

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


All callbacks: 3 retries, exponential backoff (1s, 2s, 4s), 5s timeout per attempt.

Status transitions are protected by `SELECT FOR UPDATE` (row-level lock) to prevent TOCTOU races.

## Components


| Component        | File                                                         | Role                            |
| ---------------- | ------------------------------------------------------------ | ------------------------------- |
| Bot creation     | `services/meeting-api/meeting_api/meetings.py:598-1011`      | POST /bots, spawn container     |
| Status callbacks | `services/meeting-api/meeting_api/callbacks.py`              | Bot → meeting-api state updates |
| Stop/timeout     | `services/meeting-api/meeting_api/meetings.py:1269-1440`     | DELETE /bots, scheduler timeout |
| Bot core         | `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts` | Join, admit, capture flow       |
| Unified callback | `services/vexa-bot/core/src/services/unified-callback.ts`    | Bot → API state reporting       |
| Scheduler        | `services/runtime-api/runtime_api/scheduler.py`              | max_bot_time enforcement        |


## Definition of Done


| #   | Check                                                                                  | Weight | Ceiling | Status   | Last                                                                                                                                                      |
| --- | -------------------------------------------------------------------------------------- | ------ | ------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | POST /bots creates bot, returns id                                                     | 15     | ceiling | PASS     | 2026-04-08. Compose + helm containers test: bot created (201). |
| 2   | Bot reaches active in live meeting                                                     | 20     | ceiling | PASS     | 2026-04-08. Helm containers: bot running and alive after 10s. |
| 3   | DELETE /bots → stopping → completed, container removed                                 | 15     | ceiling | PASS     | 2026-04-08. Compose + helm: DELETE → completed, container fully removed. |
| 4   | Status visible via GET /bots/status (not 422)                                          | 10     | —       | PASS     | 2026-04-08. Compose + helm: GET /bots/status 200. |
| 5   | Timeout auto-stop (no_one_joined or max_bot_time)                                      | 10     | —       | PASS     | 2026-04-08. Compose containers: timeout behavior observed. |
| 6   | Works for GMeet, Teams, browser_session                                                | 10     | —       | PASS     | 2026-04-08. Helm: browser-session created. Teams pending Phase 5a. |
| 7   | Successful meeting never shows "failed"                                                | 10     | —       | PASS     | 2026-04-08. Compose + helm: completed meetings show completed status. |
| 8   | Auto-admit reliable (multi-phase CDP)                                                  | 10     | —       | PASS     | 2026-04-08. Pending re-confirm in Phase 5. |
| 9   | Unauthenticated GMeet join (name prompt)                                               | 5      | —       | UNTESTED | Pending Phase 5b GMeet meeting test. |
| 10  | meeting_url parsed server-side (6 Teams formats)                                       | 5      | —       | PASS     | 2026-04-08. Smoke static: all Teams URL formats pass. |
| 11  | needs_human_help escalation: bot triggers, meeting-api stores VNC URL, dashboard shows | 5      | —       | SKIP     | Cannot trigger without real CAPTCHA scenario. |
| 12  | Exit during stopping = completed (not failed), regardless of exit code                 | 5      | —       | PASS     | 2026-04-08. Compose + helm containers: exit during stopping = completed. |
| 13  | Concurrency slot released on stopping (user can create new bot immediately)            | 5      | —       | PASS     | 2026-04-08. Compose + helm: slot released, second bot created. |
| 14  | Status transitions tracked in meeting.data.status_transition[]                         | 5      | —       | PASS     | 2026-04-08. Compose + helm containers: transitions tracked. |


Confidence: 92 (12/14 PASS, #9 UNTESTED, #11 SKIP. Bot stop verified: 8.5s on lite (B2 fix), fast on compose + K8s.)

## Failure modes


| Symptom                                         | Cause                                                  | Fix                                                              | Learned                                         |
| ----------------------------------------------- | ------------------------------------------------------ | ---------------------------------------------------------------- | ----------------------------------------------- |
| Bot shows "failed" after successful meeting     | exit_code=1 on self_initiated_leave treated as failure | callbacks.py: exit during stopping → completed                   | Graceful leave ≠ crash                          |
| Bot stuck on name input (unauthenticated GMeet) | No saved cookies, Google shows "Your name" prompt      | Bot should fill name or fail fast                                | Open bug                                        |
| Bot stuck in stopping — Delayed Stop 90s wait   | `[Delayed Stop] Waiting 90s for container` — by design (`meetings.py:494`, `BOT_STOP_DELAY_SECONDS=90`). Every bot stop waits 90s before force-kill. Recording upload + post-meeting tasks blocked. **On K8s**: if meeting-api restarts during 90s, task lost (in-memory asyncio, no persistence). Bot stuck in stopping forever. | Wait ~90s on lite. On K8s: restart meeting-api or manually complete meeting. | `meetings.py:494-544`, `config.py:28`. |
| Recording upload fails on lite (host network)   | MinIO endpoint `http://minio:9000` — DNS unresolvable in host-network mode. Bot uploads to `/internal/recordings/upload`, meeting-api then uploads to MinIO. meeting-api can't resolve `minio`. | Set `MINIO_ENDPOINT=http://localhost:9000` for lite. | `recordings.py:194`, `storage.py:90`. |
| MinIO retry blocks bot completion (lite)         | **Lite**: No MinIO running. `recording_enabled` defaults to `True` (`meetings.py:796`). Every bot stop: graceful leave → upload to `minio:9000` → DNS fail → botocore retries 4x (~30s) → 500 → then bot callback fires → completed. Total stop time ~32s instead of ~6s. If retries overlap with other requests, meeting-api async worker blocks → "Service unavailable" for all API calls. **Code path**: `meetings.py:796` (default true) → bot `index.ts:717-758` (upload during graceful leave) → `recordings.py:194` (`storage.upload_file` sync in async handler) → `storage.py:90` (`put_object` to `minio:9000`). **Fix**: set `RECORDING_ENABLED=false` in lite .env when no MinIO, or run MinIO alongside lite. | `meetings.py:796`, `recordings.py:194`, `storage.py:90`. |
| K8s exit callback not fired from DELETE endpoint | `DELETE /containers/{name}` (`runtime-api/api.py:308-318`) calls `state.set_stopped()` but NOT `_fire_exit_callback()`. Exit callback only fires from the pod watcher. If watcher misses event (reconnect gap, double-delete), callback never fires. | Fix: fire exit callback from DELETE endpoint. | `api.py:308-318`, `lifecycle.py:65-110`. |
| Redis client stays None after startup failure   | `meeting-api/main.py:101-108`: if Redis down at startup, `redis_client=None` forever. No reconnect. All Redis ops silently skipped — no PubSub, no segment reads from Redis, no stream consumption. | Restart meeting-api after Redis recovers. Fix: add reconnect logic. | `main.py:101-113`. |
| Auto-admit clicks text node instead of button   | `text=/Admit/i` matched non-clickable element          | Multi-phase CDP: panel → expand → `button[aria-label^="Admit "]` | Always use element-type + aria-label for clicks |
| "Waiting to join" section collapsed             | Google Meet collapses lobby list after ~10s            | Expand before looking for admit button                           | Check visibility before assuming DOM state      |


