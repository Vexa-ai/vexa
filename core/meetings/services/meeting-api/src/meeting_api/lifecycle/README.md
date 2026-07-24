# lifecycle — receiver + meeting-state machine (O-MTG-1)

The **receiver** side of `lifecycle.v1`. The bot emits LifecycleEvents to its control-plane
callback (emitter = `meetings/services/bot/src/orchestrator.ts`, L4-proven); this brick ingests
them, validates each AT THE SEAM (jsonschema by path against the sealed `lifecycle.v1` schema),
and drives each meeting record's FSM — rejecting illegal transitions.

Derived from the parent `services/meeting-api/meeting_api/{callbacks.py, schemas.py}`,
reimplemented clean for the bot's DOMAIN lifecycle (lifecycle.v1's `BotStatus`), dropping the
server-side `requested`/`stopping` states.

## The machine
```
<new>              → joining
joining            → awaiting_admission · active · failed
awaiting_admission → active · needs_help · failed
needs_help         → active · failed
active             → completed · failed
completed          ∅   (terminal)
failed             ∅   (terminal)
```
`completed` records the bot-reported `completion_reason`; `failed` records a `failure_stage`
**derived server-side from the state we were in** — never trusted from the bot's payload (the
parent's FM-003 discipline). `active → joining` (and any re-open of a terminal record) is rejected.

## Surface
- `machine.py` — `BotStatus`/`CompletionReason`/`FailureStage` (the lifecycle.v1 enums),
  `LEGAL_TRANSITIONS` + `can_transition`, `MeetingRecord`, `MeetingStore` (in-memory, no DB),
  `LifecycleSink` (`apply(event)` / `apply_change(event, transition_source=…)`), `IllegalTransition`,
  `TransitionSource`, `StatusChange`.
- `receiver.py` — `create_app(store, on_status_change)`: the FastAPI receiver.
  `POST /bots/internal/callback/lifecycle` validates → drives the FSM → emits the
  `meeting.status_change` webhook → `200 accepted` / `409 illegal-transition` / `422 schema-violation`.
- `webhook.py` — `build_status_change_envelope(change)`: wrap a `StatusChange` as a sealed
  `webhook.v1` `Envelope` (`event_type=meeting.status_change`), validated at the seam.
- `stop.py` (P3b) — the user-stop path: `request_stop(record, publisher, meeting_id)` sets
  `stop_requested` + publishes the `bot_commands:meeting:{id}` `{action:"leave"}` command;
  `classify_user_stop` / `stop_event_for` resolve the bot's exit to an ATTRIBUTED terminal.
- `stop_router.py` — the API Stop composition. For a bot that has reported
  `awaiting_admission`, it persists `withdrawal.status=pending`, publishes `leave`, and waits for
  the bot's typed cancellation/page-close acknowledgement. The callback atomically persists
  `completed` before runtime deletion. A missing acknowledgement atomically becomes `timed_out`
  before the bounded 24-second capacity fallback deletes the workload. `requested`/`joining`
  retain direct teardown because those states do not prove the bot subscribed to the command
  channel; active stops retain the normal recording-finalization path.
- `retry.py` (P3d) — `JoinRetryController` + the transient/permanent taxonomy
  (`classify_retry` / `is_transient`): on a TRANSIENT join-failure schedule a fresh re-spawn (a new
  `meeting_session`) through the runtime scheduler with bounded exponential backoff; a PERMANENT
  reason never retries.

## P3a — lifecycle diagnostics (attributable reasons)
Every FSM advance captures `completion_reason` · `failure_stage` (DERIVED SERVER-SIDE from the
record's status at write-time, never the bot's stale payload) · `error_details` · a
`status_transition[]` trail (one entry per hop: `{from, to, timestamp, source, [reason,
completion_reason, failure_stage, error_details]}`) · terminal forensics (`bot_logs` capped at 50 KiB
trimmed oldest-first + `bot_resources`). `MeetingRecord.data` is the `meeting.data` JSONB projection
the parent persists. Each advance returns a `StatusChange` carrying the `meeting.status_change`
webhook body `{old_status, new_status, reason, transition_source ∈ user_stop|bot_callback|scheduler_timeout}`.

## P3d taxonomy
TRANSIENT → retry: `awaiting_admission_timeout`, `join_failure`. PERMANENT → no retry → failed:
`awaiting_admission_rejected`, `evicted`, `validation_error`, `max_bot_time_exceeded`, user `stopped`
(+ `left_alone`/`startup_alone`, which are normal outcomes). Unknown/None → PERMANENT (fail-safe).

## Evals
`tests/test_lifecycle_machine.py` (FSM) · `test_lifecycle_http.py` (receiver) ·
`test_lifecycle_diagnostics.py` (P3a: per-terminal-cause attribution + webhook) ·
`test_lifecycle_fixtures.py` (P3b: normal / user-stop+leave-command / join-failure) ·
`test_join_retry.py` (P3d: FakeClock-driven bounded retries). Ride `gate:python`.
