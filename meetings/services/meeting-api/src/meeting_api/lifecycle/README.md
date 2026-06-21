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
  `LifecycleSink` (the port: `apply(event)`), `IllegalTransition`.
- `receiver.py` — `create_app(store)`: the FastAPI receiver. `POST /bots/internal/callback/lifecycle`
  validates → drives the FSM → `200 accepted` / `409 illegal-transition` / `422 schema-violation`.
  `GET /health` is the receiver `gate:health` will point at (orchestrator wires the gate). `conforms`
  is the `_conforms`-style seam validator.

## Evals
`tests/test_lifecycle_machine.py` (FSM directly) + `tests/test_lifecycle_http.py` (TestClient).
Ride `gate:python`.
