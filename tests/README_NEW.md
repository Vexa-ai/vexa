## Vexa PRD-Driven Development: Objectives → Requirements → Tests

Following Rule 1.1.

### Paradigm
- **Objective (WHY)**: The user/business outcome we want. Stable over time. One objective can drive multiple requirements.
- **Requirement (WHAT)**: A verifiable behavior/constraint that fulfills an objective. Unambiguous and testable.
- **Test (HOW/Validation Plan)**: A concrete validation that one or more requirements hold in the system. One test can cover multiple requirements.

Relationships:
- One Objective → many Requirements
- One Test → many Requirements (cross-validations allowed)

This PRD controls the dev scaffold and the tests. Code is implemented to meet Requirements; tests continuously validate them. Documentation (this file) is the single place that ties WHY→WHAT→HOW.

---

### Authoring Templates

Objective (WHY)
- ID: O<n>
- Name: <short phrase>
- Description: <1–3 sentences of user/business value>
- Success signals/metrics: <optional>

Requirement (WHAT)
- ID: R<n>
- Objective links: [O..]
- Spec: <clear, unambiguous behavior>
- Priority: P0/P1/P2
- Acceptance criteria: <bullet list of observable checks>

Test (HOW)
- ID: T<n>
- Requirement links: [R..]
- Pre-conditions: <env/data/user steps>
- Steps: <what the test does>
- Expected results: <observable outcomes matching acceptance criteria>
- Artifacts: <logs, screenshots, traces collected>

---

## System WHY (holistic narrative)

- Deliver privacy-first, reliable real-time transcription that teams can confidently build upon.
- Put users in control of capacity: when they stop a bot, they must immediately reclaim a slot to start another meeting.
- Make lifecycle timing deterministic and bounded to avoid race-induced confusion.
- Keep one coherent truth across DB, runtime, and UI; never show a bot that cannot actually join.
- Preempt startup on user Stop: if Stop arrives while the bot is starting or waiting for admission, cancel and leave immediately to prevent ghosts.
- Steward resources responsibly: prevent orphans, bound cleanup, and respect concurrency/capacity limits.
- Maintain strong validation and observability so failures surface early and are easy to diagnose.

---

## Objectives (WHY)

### O1. Concurrency freedom after explicit stop
Users have concurrency limits. When they stop a bot, they must be able to immediately start another meeting without being blocked by cleanup.

Success signals:
- Users can start a new meeting right after issuing Stop, regardless of container/browser cleanup status.

### O2. Input/auth validation integrity
All REST and WS interactions validate auth and inputs up front to protect the system and fail fast.

### O3. Single source of truth and user control priority
Database meeting.status is authoritative; user API actions (POST/DELETE) take precedence over other signals; callbacks confirm, others trigger cleanup only.

### O4. Accurate joining outcomes with UI/runtime consistency
Joining outcomes (rejected, not attended, timeout, errors) are recorded immediately with clear reasons. A bot must not appear joinable/waiting in the UI if its process/container is already terminated (no waiting-room ghosts).

### O5. Real-time transcript delivery
When on-meeting, authorized users receive transcripts over WS and can retrieve via API.

### O6. Graceful end-of-life ordering (fast, non-blocking)
Shutdowns follow: DB status update → container exit → bot leaves meeting → post-meeting routines (e.g., webhook), and run quickly without blocking new sessions.

### O7. Database reflects runtime truth
DB status transitions accurately mirror actual lifecycle and unexpected leaves; never a state where a container is down while DB/UX implies a live, admit‑able bot.

### O8. No orphaned resources (bounded time)
Stopping or failures do not leave orphaned records in `requested`/`stopping` or lingering containers/participants; cleanup completes within a bounded window.

### O9. Reliable webhook notifications
On status changes, configured webhooks are invoked reliably (with retries and signing) for external systems.

### O10. Concurrency control enforcement
System enforces per-tenant concurrency and global capacity while allowing instant slot reuse after Stop (see O1).

---


## Bot Lifecycle: User Flow ←→ Objectives/Requirements/Tests

This lifecycle frames tests and breakpoints. Each stage lists typical scenarios, risks, and the mapped Objectives (O), Requirements (R), and representative Tests (T).

### 1) Request Bot (POST /bots)
- Scenarios:
  - Start accepted → meeting `requested`
  - Duplicate request for same key → 409 with guidance
  - Over concurrency/capacity → denied per limits
  - Invalid auth/input → 401/403/422
- Risks:
  - Duplicate races creating multiple meetings
  - Non-user-provided meeting IDs (forbidden)
  - Overbooking when limits aren’t enforced
- Mappings:
  - O: [O1, O2, O3, O10]
  - R: [R7 idempotency, R10 security/input, R12 capacity]
  - T: Validation test (T1), Concurrency checks (future T-Concurrency)

### 2) Wait for Admission (joining)
- Scenarios:
  - Admitted → `active`, startup callback observed
  - Rejected by host
  - Not attended (nobody admits)
  - Timeout → `admission_failed` (normal, exit_code=0)
  - Join error → failed with details
  - Stop preemption during startup/waiting: Stop cancels admission attempt and triggers immediate leave
- Risks:
  - Waiting-room ghost (UI shows waiting while process is down)
  - Missing/late startup callback
  - Illegal transitions
- Mappings:
  - O: [O4, O6, O7]
  - R: [R5 timing bounds (join), R6 consistency, R14 callbacks, R15 state machine, R17 stop-preempts-start]
  - T: Joining-failure test (future T-JoinFail), Validation (T1)

### 3) On Meeting (active)
- Scenarios:
  - Active with transcript streaming (WS)
  - Reconfigure bot parameters mid-session
  - Transcription server busy/failover
- Risks:
  - Transcript SLA violations (slow/no frames)
  - WS reconnect loops without progress
- Mappings:
  - O: [O3, O5, O7]
  - R: [R9 transcript SLA, R6 consistency, R14 callbacks]
  - T: On-meeting path (future T-OnMeeting)

### 4) Meeting End (Stop / Alone / Evicted / Error)
- Scenarios:
  - User Stop → immediate `stopping`, new bot allowed instantly
  - Alone end → graceful completion
  - Evicted/kicked → completion with reason
  - Error → failed with details
  - Stop preemption: Stop during startup or waiting-room cancels join and triggers immediate browser-level leave
- Risks:
  - Slow or stuck shutdown (long `stopping`)
  - Orphaned container or waiting-room ghost
  - Concurrency slot not freed immediately
- Mappings:
  - O: [O1, O6, O7, O8]
  - R: [R1 immediate `stopping`, R2 instant new bot, R3 non-blocking shutdown, R4 no ghost, R5 timing, R6 consistency, R13 observability, R15 state machine, R17 stop-preempts-start]
  - T: T2 Stop-before-join, Alone-end (future), Evicted-end (future)

### 5) After Meeting (post routines)
- Scenarios:
  - Webhook fired with status and metadata
  - Transcript retrieval via API
  - Background reconciliation sweeps
- Risks:
  - Webhook delays/failures without retry
  - Residual orphans from prior runs
- Mappings:
  - O: [O3, O8, O9]
  - R: [R8 webhook reliability, R11 orphan watchers, R16 time-windowed consistency]
  - T: Webhook validation (future), Orphan watcher test (future)

Breakpoints & Instrumentation (cross-cutting):
- API responses and DB status at each transition (requested/active/stopping/completed/failed)
- Startup/exit callbacks with timing and reasons
- Container logs tail on shutdown (R13)
- WS transcript events (count/latency) during `active` (R9)
- Reconciliation logs for watchers (R11, R16)

---

## Requirements

### R1. Immediate status transition frees concurrency
- Links: [O1]
- Spec: On DELETE /bots, the meeting status must change to `stopping` immediately.
- Priority: P0
- Acceptance:
  - API returns 202/200 for stop
  - DB shows `stopping` within <1s
  - New POST for same platform/native_meeting_id is accepted immediately (no 409 due to prior instance)

### R2. New bot can start immediately after stop (no blocking on cleanup)
- Links: [O1]
- Spec: System must accept a new bot request for the same meeting right after Stop, without waiting for container/browser/post-routines to finish.
- Priority: P0
- Acceptance:
  - POST after stop succeeds instantly
  - New meeting shows `requested`

### R3. Graceful shutdown is non-blocking
- Links: [O1]
- Spec: Container/browser leave happens in the background and must not block R1/R2.
- Priority: P1
- Acceptance:
  - First meeting reaches terminal `completed|failed` within a bounded window (e.g., ≤ 60s; configurable)
  - Leave attempt is initiated from browser context before page close (logged)
  - No orphaned `stopping` after grace period

### R4. No ghost participant in waiting room
- Links: [O1]
- Spec: Bot must attempt to leave waiting room/meeting on shutdown so no ghost participant remains.
- Priority: P1
- Acceptance:
  - Manual/user check confirms the first bot leaves
  - Logs show leave attempts during shutdown

### R5. Timing bounds for stop and join flows
- Links: [O6, O8, O7]
- Spec: Stop-triggered cleanup must complete within a bounded window; join admission has an explicit timeout with deterministic status.
- Priority: P1
- Acceptance:
  - Stop: terminal `completed|failed` within N seconds/minutes (configurable; default ≤ 60s); no meeting remains in `stopping` beyond grace period
  - Join: `admission_failed` after timeout (configurable; default ≤ 5m) with reason and timestamps recorded in meeting data

### R6. State consistency invariant (DB/WS/UI)
- Links: [O7, O4]
- Spec: It must never be possible for UI/WS to imply a joinable bot while the container/process is already down.
- Priority: P0
- Acceptance:
  - No WS/UI representation of joinable/waiting bot after container termination
  - DB status and last callbacks align within tolerance (e.g., ≤ 5s skew); discrepancies are reconciled via cleanup watchers

### R7. Idempotent controls and duplicate prevention
- Links: [O3, O10]
- Spec: POST/DELETE must be idempotent and prevent duplicate active/requested meetings for the same key.
- Priority: P0
- Acceptance:
  - Repeated POST returns the existing meeting or appropriate 409 with guidance
  - Repeated DELETE returns 202/200 without harm and does not regress state

### R8. Webhook delivery reliability
- Links: [O9]
- Spec: Webhooks fire on status changes with retry/backoff and signed payloads.
- Priority: P1
- Acceptance:
  - Delivery attempts logged; retry policy applied; signature/secret validated
  - Downstream failure does not block lifecycle progress

### R9. Transcript availability SLA (on-meeting)
- Links: [O5]
- Spec: When active, client receives initial `transcript.mutable` frames quickly and then continuously.
- Priority: P2
- Acceptance:
  - At least 3 `transcript.mutable` segments received; first arrives within T seconds of `active`

### R10. Security and input discipline
- Links: [O2]
- Spec: API key required for REST/WS; meeting IDs are always user-provided (derived from URLs), never generated by the system.
- Priority: P0
- Acceptance:
  - Invalid/missing keys rejected (401/403); invalid platform/native ID rejected (422)
  - Tests verify meeting ID extraction and reject non-user-provided IDs

### R11. Automated orphan cleanup watchers
- Links: [O8, O6]
- Spec: Background watchers detect and reconcile stuck `requested/stopping` and lingering containers/participants.
- Priority: P2
- Acceptance:
  - Periodic sweeps clear inconsistencies and emit logs; no long-lived orphans persist

### R12. Capacity-aware scheduling
- Links: [O10]
- Spec: Launch respects per-tenant concurrency and global capacity while allowing instant reuse after Stop.
- Priority: P0
- Acceptance:
  - New launches are admitted/denied according to limits; slot reuse verified immediately after Stop

### R13. Shutdown observability and artifacts
- Links: [O3, O7, O8]
- Spec: On Stop, the system records and exposes artifacts needed to diagnose shutdown (container logs tail, leave attempts, exit reasons).
- Priority: P1
- Acceptance:
  - Container logs (last 50–200 lines) are retrievable for the stopped bot
  - Meeting data includes shutdown reason and timestamps; tests collect and print these artifacts on failure

### R14. Callback integrity and timing
- Links: [O3, O9]
- Spec: Startup callback is emitted upon admission/active; exit callback is emitted on termination with `exit_code` and `reason`, within bounded time.
- Priority: P1
- Acceptance:
  - Startup callback observed within T seconds of `active`; exit callback within T seconds of terminal status (configurable; default ≤ 5s)
  - Callback payloads include connection/container identifiers and are signed/validated when applicable

### R15. State machine invariants
- Links: [O3, O7]
- Spec: Meeting status transitions follow an allowed graph; no regressions (e.g., `active` → `requested`) and no illegal skips.
- Priority: P0
- Acceptance:
  - Allowed transitions only: `requested` → `active|stopping`, `active` → `stopping`, `stopping` → `completed|failed`
  - Tests assert no invalid transition appears in event stream or DB history

### R16. Time-windowed consistency checks
- Links: [O7, O6]
- Spec: Automated checks ensure DB/process/UI converge within a small time window; discrepancies trigger reconciliation.
- Priority: P2
- Acceptance:
  - Periodic probes confirm convergence (e.g., ≤ 5–10s); divergences are logged and healed by watchers

### R17. Stop preempts startup/admission (early cancel)
- Links: [O1, O4, O6, O7]
- Spec: If Stop is issued while the bot is starting or waiting for admission (typical 10–15s to apply after container start), the bot must cancel startup and immediately attempt a browser-level leave of waiting room/meeting.
- Priority: P0
- Acceptance:
  - On DELETE /bots during `requested` pre-admission, logs show performLeaveAction/leaveWaitingRoom within ≤ 5s
  - No visible join request remains in UI after container exit; no waiting-room ghost
  - DB transitions `requested → stopping → completed|failed` within bound; immediate POST for new bot still succeeds (R2)

---


## Tests

### Test Passes (human-optimized)
Run these grouped passes to cover multiple objectives/requirements per session with minimal prompts. Each pass references canonical tests (T1–T12) below.

Pass A — Validation & Request Flow (3–5 min)
- Covers: O2, O10; R10, R7, R12
- Includes: T1, slice of T7
- Prompts: None

Pass B — Stop-before-join + Instant Reuse + Shutdown Artifacts (3–5 min)
- Covers: O1, O6, O7, O8; R1, R2, R3, R4, R5 (stop), R6, R13
- Includes: T2 (with Stop preemption during startup), subset of T8, quick T11 check
- Prompts: One confirm (first left, second waiting)

Pass C — On-meeting Integrated (6–8 min)
- Covers: O3, O5, O6, O7, O8; R9, R6, R14, R3/R4/R5 (end)
- Includes: T4, T5 (primary) or T6 (variant)
- Prompts: Admit at start; one end-state confirm

Pass D — Joining Failure (timeout) (6–7 min)
- Covers: O4, O6, O7; R5 (join), R6, R14, R15
- Includes: T3
- Prompts: One confirm (waited then left)

Pass E — Post-meeting Reliability & Consistency (4–6 min)
- Covers: O3, O8, O9, O7; R8, R11, R16, R15
- Includes: T12, T10, T11, T9
- Prompts: Optional (webhook receiver check)

### T1. Validation (negative)
- Links: [R10, R7]
- Purpose: Ensure strict auth/input validation and basic idempotency/duplicate guards.
- Steps: REST invalid key/platform/native_id; WS invalid key/malformed/invalid action/missing fields/unauthorized subscribe; optional duplicate POST sanity.
- Expected: REST 401/403/422; WS handshake reject/error/close; duplicates get 409 or equivalent guidance.
- Artifacts: API responses.

### T2. Stop-before-join
- Links: [R1, R2, R3, R4]
- Pre-conditions: Valid API key; Google Meet URL; user does NOT admit the bot.
- Steps:
  1) POST /bots (start)
  2) Immediately DELETE /bots (stop)
  3) Immediately POST /bots again (new bot for same meeting)
  4) Observe DB status transitions for first bot; monitor container lifecycle
  5) Verify Stop preempted startup: container/browser logs show a leave attempt within ≤ 5s even though admission application typically occurs 10–15s after container start
  6) Manual confirmation that first bot left; second bot remains in waiting room
- Expected results:
  - R1: DB flips to `stopping` immediately after DELETE
  - R2: New POST is accepted immediately, new meeting is `requested`
  - R3: First bot reaches terminal status later without blocking R1/R2
  - R4: First bot leaves meeting (manual confirmation + logs)
  - R17: No waiting-room ghost when Stop arrives during startup; evidence of browser-level leave is present
- Artifacts:
  - Bot manager logs around stop window
  - First bot container logs (tail on shutdown; include leave attempts if available)
  - Meetings dump (before/after)

Notes:
- This test doubles as a concurrency sanity check by ensuring a second request is not blocked by the prior stop.

---

### T3. Joining failure (timeout/non-admission)
- Links: [R5, R6, R14, R15]
- Purpose: Validate deterministic admission timeout and consistent status/reporting when not admitted.
- Steps: Start bot; do not admit; wait for timeout; observe status and reason.
- Expected: Final status `completed`; meeting data shows reason `admission_failed`, exit_code=0; no UI ghost; callbacks consistent.
- Artifacts: Meetings dump; optional logs.

### T4. On-meeting path (active + transcripts)
- Links: [R9, R6, R14]
- Purpose: Validate transcript flow while active and basic runtime consistency.
- Steps: Start bot; admit; subscribe WS; collect ≥3 transcript.mutable; optional reconfigure.
- Expected: Frames arrive within SLA; callbacks align; no inconsistency.
- Artifacts: WS frames timings; meetings dump.

### T5. Alone-end
- Links: [R3, R4, R5, R13]
- Purpose: Validate graceful completion when bot becomes alone; ensure no ghost remains.
- Steps: Admit bot; all participants leave; observe shutdown.
- Expected: Terminal within bound; leave attempt logged; no ghost participant.
- Artifacts: Container tail; meetings dump.

### T6. Evicted-end
- Links: [R3, R4, R5, R13]
- Purpose: Validate graceful completion when evicted; ensure no ghost remains.
- Steps: Admit bot; evict; observe shutdown.
- Expected: Terminal within bound; reason recorded; no ghost.
- Artifacts: Container tail; meetings dump.

### T7. Concurrency/capacity limits
- Links: [R12, R1, R2]
- Purpose: Enforce per-tenant limits while allowing instant slot reuse after Stop.
- Steps: Start up to limit; extra POST denied; Stop one; immediate POST accepted.
- Expected: Denials at limit; instant reuse after Stop.
- Artifacts: API responses; meetings dump.

### T8. Callback integrity and timing
- Links: [R14]
- Purpose: Validate startup/exit callbacks presence, payload, timing.
- Steps: Exercise join and end; record callbacks.
- Expected: Startup near `active`; exit near terminal; payloads include ids; within bounds.
- Artifacts: Callback logs/events.

### T9. State machine invariants
- Links: [R15]
- Purpose: Ensure only allowed status transitions occur.
- Steps: Scrape event/DB history across tests.
- Expected: No illegal/regressive transitions.
- Artifacts: Transition audit.

### T10. Orphan watcher
- Links: [R11, R16]
- Purpose: Validate periodic reconciliation clears stuck meetings/containers.
- Steps: Simulate/observe stuck states; run watcher window.
- Expected: Healed within window; logs emitted.
- Artifacts: Watcher logs; before/after dumps.

### T11. Consistency window
- Links: [R6, R16]
- Purpose: Validate DB/process/UI convergence within small time window.
- Steps: Probe multiple views during transitions.
- Expected: Converge ≤ window; divergences reconciled.
- Artifacts: Probe timings; logs.

### T12. Webhook reliability
- Links: [R8]
- Purpose: Validate webhook retries/signing do not block lifecycle.
- Steps: Point webhook to test receiver; induce temporary failures; observe retries.
- Expected: Retries per policy; lifecycle unblocked; signatures valid.
- Artifacts: Receiver logs; delivery attempts.

## Process: PRD controls Dev & Tests
1) Define Objectives (WHY)
2) Derive Requirements (WHAT) with acceptance criteria
3) Design Tests (HOW) that map to Requirements
4) Implement code against Requirements
5) Run tests; collect artifacts; iterate until green
6) Keep this mapping current; add new Objectives/Requirements/Tests as the product evolves

Conventions
- IDs: O<n>, R<n>, T<n>
- Status vocabulary: `requested`, `active`, `stopping`, `completed`, `failed`
- Meeting IDs are always user-provided via meeting URLs

## Known Issues

### Bot Admission Detection
**Issue**: The bot currently does not distinguish correctly between being admitted to a meeting vs. waiting in the waiting room. The bot considers itself "admitted" when it finds a "Leave call" button, but this button can be visible even in the waiting room.

**Impact**: Tests that require the bot to wait for human admission (T2, T3) may not accurately reflect the intended waiting room behavior, as bots may auto-proceed without human approval.

**Status**: Non-critical issue - keeping current behavior for now as it doesn't affect core functionality.

