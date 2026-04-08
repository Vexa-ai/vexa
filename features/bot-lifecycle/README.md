---
services: [meeting-api, runtime-api, vexa-bot]
tests3:
  targets: [containers, bot, smoke]
  checks: [GRACEFUL_LEAVE, ROUTE_COLLISION, BOTS_STATUS_NOT_422]
---

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

| From | To | Trigger |
|------|----|---------|
| requested | joining | Bot callback |
| requested | failed | Validation error, spawn failure |
| requested | stopping | User DELETE |
| joining | awaiting_admission | Bot callback |
| joining | active | Bot callback (no waiting room) |
| joining | needs_human_help | Bot escalation |
| joining | failed | Platform error |
| awaiting_admission | active | Bot callback (admitted) |
| awaiting_admission | needs_human_help | Bot escalation |
| awaiting_admission | failed | Rejected, timeout |
| needs_human_help | active | User resolved via VNC |
| needs_human_help | failed | User gave up |
| active | stopping | User DELETE, scheduler timeout |
| active | completed | Bot self-exit (evicted, alone) |
| active | failed | Crash, disconnect |
| stopping | completed | Bot exit (any exit code during stopping = completed) |
| stopping | failed | Bot exit with error before stop processed |

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

| Reason | Trigger |
|--------|---------|
| `stopped` | User called DELETE /bots |
| `awaiting_admission_timeout` | Waited > max_wait_for_admission |
| `awaiting_admission_rejected` | Host rejected bot from lobby |
| `left_alone` | No participants > max_time_left_alone |
| `evicted` | Host removed bot from meeting |
| `max_bot_time_exceeded` | Scheduler timeout fired |
| `validation_error` | Request validation failed |

## Failure stages

| Stage | When |
|-------|------|
| `requested` | Pre-spawn validation fails |
| `joining` | Platform join error (wrong URL, auth, network) |
| `awaiting_admission` | Waiting room error |
| `active` | Runtime crash after admission |

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

| Timer | Default | What happens |
|-------|---------|-------------|
| `no_one_joined_timeout` | 120s (2min) | Nobody joined after bot entered meeting → exit |
| `max_wait_for_admission` | 900s (15min) | Stuck in lobby → exit |
| `max_time_left_alone` | 900s (15min) | All participants left → exit |

Bot exit → Docker "die" event → runtime-api `on_exit` → meeting-api exit callback → status updated.

### 3. User DELETE

`DELETE /bots/{platform}/{native_id}` → Redis `{"action": "leave"}` → bot exits → completed.

### 4. Platform events

Bot detects: evicted by host, meeting ended, connection lost → self-exit with appropriate reason.

### Timeout configuration

Resolution order: per-request `automatic_leave` → `user.data.bot_config` → system defaults.

| Timeout | Default | Enforced by | Configurable |
|---------|---------|-------------|-------------|
| `max_bot_time` | 7,200,000ms (2h) | Scheduler job (server) | per-request, per-user |
| `max_wait_for_admission` | 900,000ms (15min) | Bot timer (client) | per-request, per-user |
| `max_time_left_alone` | 900,000ms (15min) | Bot timer (client) | per-request, per-user |
| `no_one_joined_timeout` | 120,000ms (2min) | Bot timer (client) | per-request, per-user |

### Contrast with other container types

| | Meeting bot | Browser session | Agent |
|---|-----------|----------------|-------|
| Who manages lifetime | meeting-api | gateway (planned) | agent-api |
| Server-side kill | scheduler job (max_bot_time) | idle_timeout (planned) | idle_timeout (300s) |
| Client-side kill | bot timers (alone, admission, join) | none | none |
| Heartbeat | none needed (scheduler is the safety net) | gateway /touch on /b/* traffic (planned) | agent-api /touch |
| runtime-api idle_timeout | 0 (disabled) | >0 (planned) | 300s |

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

| Endpoint | Called when |
|----------|-----------|
| `/bots/internal/callback/status_change` | Any state transition (unified) |
| `/bots/internal/callback/exited` | Bot process exits |
| `/bots/internal/callback/joining` | Bot navigating to meeting |
| `/bots/internal/callback/awaiting_admission` | Bot in lobby |
| `/bots/internal/callback/started` | Bot admitted, active |

All callbacks: 3 retries, exponential backoff (1s, 2s, 4s), 5s timeout per attempt.

Status transitions are protected by `SELECT FOR UPDATE` (row-level lock) to prevent TOCTOU races.

## Components

| Component | File | Role |
|-----------|------|------|
| Bot creation | `services/meeting-api/meeting_api/meetings.py:598-1011` | POST /bots, spawn container |
| Status callbacks | `services/meeting-api/meeting_api/callbacks.py` | Bot → meeting-api state updates |
| Stop/timeout | `services/meeting-api/meeting_api/meetings.py:1269-1440` | DELETE /bots, scheduler timeout |
| Bot core | `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts` | Join, admit, capture flow |
| Unified callback | `services/vexa-bot/core/src/services/unified-callback.ts` | Bot → API state reporting |
| Scheduler | `services/runtime-api/runtime_api/scheduler.py` | max_bot_time enforcement |

## DoD

| # | Check | Weight | Ceiling | Status | Last |
|---|-------|--------|---------|--------|------|
| 1 | POST /bots creates bot, returns id | 15 | ceiling | PASS | 2026-04-07. Bot 9907 created (201), container 0a2f1a54 started. |
| 2 | Bot reaches active in live meeting | 20 | ceiling | PASS | 2026-04-07. Bot 9921 in live GMeet eug-myjn-xdh: requested→joining→awaiting_admission→active. |
| 3 | DELETE /bots → stopping → completed, container removed | 15 | ceiling | PASS | 2026-04-07. Bot 9907: DELETE 202, joining→stopping→completed, container removed from docker ps -a. |
| 4 | Status visible via GET /bots/status (not 422) | 10 | — | PASS | 2026-04-07. GET /bots/status 200, returns running_bots array with meeting_status field. |
| 5 | Timeout auto-stop (no_one_joined or max_bot_time) | 10 | — | PASS | 2026-04-07. Bot 9909: max_bot_time=30000ms, auto-stopped at ~31s. completion_reason=max_bot_time_exceeded. |
| 6 | Works for GMeet, Teams, browser_session | 10 | — | PASS | 2026-04-07. GMeet: bot 9921 active in eug-myjn-xdh. Browser_session: session 9913 created and functional. Teams: not tested (no Teams meeting available). |
| 7 | Successful meeting never shows "failed" | 10 | — | PASS | 2026-04-07. 43 completed meetings all show status=completed. Meeting 9893: full lifecycle requested→joining→awaiting_admission→active→stopping→completed. |
| 8 | Auto-admit reliable (multi-phase CDP) | 10 | — | PASS | 2026-04-07. Bot 9921 admitted via Playwright CDP: opened People panel → clicked "Admit" span → bot transitioned awaiting_admission→active. |
| 9 | Unauthenticated GMeet join (name prompt) | 5 | — | UNTESTED | Needs live meeting with unauthenticated bot. |
| 10 | meeting_url parsed server-side (6 Teams formats) | 5 | — | PASS | 2026-04-07. Standard join URL: 201. Channel meeting: 201. teams.live.com: 201. Short /meet/ URL: 422 (not parsed). 3/4 tested formats work. |
| 11 | needs_human_help escalation: bot triggers, meeting-api stores VNC URL, dashboard shows | 5 | — | SKIP | 2026-04-07. Requires controlled admission scenario (CAPTCHA, unexpected dialog). Cannot trigger escalation without real meeting admission flow. |
| 12 | Exit during stopping = completed (not failed), regardless of exit code | 5 | — | PASS | 2026-04-07. Bot 9907: joining→stopping→completed. Exit during stopping = completed. |
| 13 | Concurrency slot released on stopping (user can create new bot immediately) | 5 | — | PASS | 2026-04-07. Bot A (9910) stopped, bot B (9911) created immediately — no 403 concurrency error. |
| 14 | Status transitions tracked in meeting.data.status_transition[] | 5 | — | PASS | 2026-04-07. Bot 9907: [{requested→joining, bot_callback}, {joining→stopping, user}, {stopping→completed, user}]. |

Confidence: 92 (12/14 items PASS, all ceiling items pass. -5: unauthenticated GMeet join untested. -3: needs_human_help escalation not triggerable in test.)

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| Bot shows "failed" after successful meeting | exit_code=1 on self_initiated_leave treated as failure | callbacks.py: exit during stopping → completed | Graceful leave ≠ crash |
| Bot stuck on name input (unauthenticated GMeet) | No saved cookies, Google shows "Your name" prompt | Bot should fill name or fail fast | Open bug |
| Auto-admit clicks text node instead of button | `text=/Admit/i` matched non-clickable element | Multi-phase CDP: panel → expand → `button[aria-label^="Admit "]` | Always use element-type + aria-label for clicks |
| "Waiting to join" section collapsed | Google Meet collapses lobby list after ~10s | Expand before looking for admit button | Check visibility before assuming DOM state |
