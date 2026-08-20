"""The auto-join re-dispatch DECISION MATRIX — every terminal outcome × its retry decision.

The defect this closes was witnessed once (stage rev 192: meeting 26312 ended ``completed``/
``stopped`` at 22:28:31, sync recreated the occurrence as 26313 at 22:28:46, and the sweep sent a
fresh bot at 22:28:47 into a meeting the founder had deliberately ended). Fixing and replaying that
one incident would leave its whole CLASS open — the same shape exists for a stop pressed in the
lobby, for a host who rejected the bot, for a signed-out profile. So the deliverable is the table,
not the case: every terminal outcome the codebase can emit gets a row here and a test of its own.

Each cell drives the REAL seam end to end — seed a terminal row of that shape, sync the same feed,
run the sweep past its backoff and inside the grace — and asserts whether a bot goes back in. The
backoff is deliberately spent in every cell (``auto_join_last_attempt`` at the occurrence start,
swept 301s later) so each cell isolates ELIGIBILITY from RATE: #1200 answered "how soon may we try
again", and this answers "may we ever".

``test_matrix_covers_every_reason_the_codebase_can_emit`` is the loud failure a future outcome
trips: add a value to ``CompletionReason`` (or another out-of-enum reason to ``occurrence``) without
deciding its row here and this file fails, naming the value.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from meeting_api.calendar_sync import parse_ics, sync_user
from meeting_api.lifecycle.machine import CompletionReason, dominant_completion_reason
from meeting_api.lifecycle.occurrence import (
    KNOWN_REASONS,
    Disposition,
    disposition,
)
from test_calendar_sync import START, USER, _event, _ics, _storm_rig, _sweep

# Decisions, named as the matrix column they select.
RETRY = Disposition.RETRY                # eligible again once the backoff elapses
SERVED = Disposition.SERVED              # the bot reached the room — never again
USER_STOPPED = Disposition.USER_STOPPED  # the user said no — never again


class Cell:
    """One row of the matrix: a terminal shape and the decision it must produce."""

    def __init__(self, name: str, *, status: str, reason=None, stage=None,
                 stop_requested: bool = False, expect: Disposition, why: str) -> None:
        self.name = name
        self.status = status
        self.reason = reason
        self.stage = stage
        self.stop_requested = stop_requested
        self.expect = expect
        self.why = why

    @property
    def dispatches(self) -> bool:
        return self.expect is Disposition.RETRY

    def data(self, scheduled_at: str) -> dict:
        data = {
            "calendar_uid": "uid-1",
            "auto_join": True,
            "scheduled_at": scheduled_at,
            # Every cell has ALREADY spent a dispatch: the question under test is eligibility,
            # never how recently we tried.
            "auto_join_last_attempt": scheduled_at,
        }
        if self.reason is not None:
            data["completion_reason"] = self.reason
        if self.stage is not None:
            data["failure_stage"] = self.stage
        if self.stop_requested:
            data["stop_requested"] = True
        return data

    def __repr__(self) -> str:  # pragma: no cover — pytest id only
        return self.name


# ---------------------------------------------------------------------------------------------
# THE MATRIX. Columns: terminal status · completion_reason · failure_stage · user-stop evidence
# → decision. Ordered by decision so the shape of the table is readable as a table.
# ---------------------------------------------------------------------------------------------
MATRIX: list[Cell] = [
    # ---- RETRY: the bot never reached the room and the failure is not the room's answer -------
    Cell("failed/join_failure@joining", status="failed", reason="join_failure", stage="joining",
         expect=RETRY, why="the transient join fault #1200 was built for — the bot never got in"),
    Cell("failed/join_failure@requested", status="failed", reason="join_failure",
         stage="requested", expect=RETRY,
         why="died before it even reported — nothing about the meeting refused us"),
    Cell("failed/awaiting_admission_timeout", status="failed",
         reason="awaiting_admission_timeout", stage="awaiting_admission", expect=RETRY,
         why="nobody let it out of the lobby in time; TRANSIENT in retry.py — the host may be late"),
    Cell("failed/start_failed", status="failed", reason="start_failed", stage="requested",
         expect=RETRY,
         why="fail_meeting's out-of-enum reason: the WORKLOAD never started, the most retryable "
             "failure there is"),
    Cell("failed/no-reason", status="failed", reason=None, stage=None, expect=RETRY,
         why="no producer emits this today; retryable only because the admitted shape is already "
             "claimed by failure_stage=active below — this is the pre-#1200 status quo, "
             "backoff-bounded"),

    # ---- SERVED: the bot reached the room, or the room's answer was no -----------------------
    Cell("completed/left_alone", status="completed", reason="left_alone", expect=SERVED,
         why="the meeting ended and the bot left — the ordinary success"),
    Cell("completed/startup_alone", status="completed", reason="startup_alone", expect=SERVED,
         why="it got in and found an empty room; a real outcome, not a fault to retry"),
    Cell("completed/evicted", status="completed", reason="evicted", expect=SERVED,
         why="someone REMOVED it from the meeting — sending it back is the rudest possible retry"),
    Cell("completed/max_bot_time_exceeded", status="completed", reason="max_bot_time_exceeded",
         expect=SERVED, why="it hit its own time cap; a fresh bot would restart the same clock"),
    Cell("completed/no-reason", status="completed", reason=None, expect=SERVED,
         why="completed is reachable ONLY from ACTIVE (machine.LEGAL_TRANSITIONS) — the bot was "
             "in the room whether or not anything recorded why it left"),
    Cell("failed/awaiting_admission_rejected", status="failed",
         reason="awaiting_admission_rejected", stage="awaiting_admission", expect=SERVED,
         why="the host said NO; PERMANENT in retry.py — a retry re-knocks on a slammed door"),
    Cell("failed/auth_session_missing", status="failed", reason="auth_session_missing",
         stage="joining", expect=SERVED,
         why="signed-out profile; a re-spawn hits the same dead cookie jar"),
    Cell("failed/validation_error", status="failed", reason="validation_error", stage="requested",
         expect=SERVED, why="the request itself is malformed — identical on every retry"),
    Cell("failed/join_failure@active", status="failed", reason="join_failure", stage="active",
         expect=SERVED,
         why="attributed to the ACTIVE stage: the bot WAS admitted (orchestrator's pipeline-start "
             "failure) and a retry would be a second visit, not a retry"),
    Cell("failed/no-reason@active", status="failed", reason=None, stage="active", expect=SERVED,
         why="positive evidence of admission with no reason recorded — the stage decides"),
    Cell("failed/left_alone", status="failed", reason="left_alone", stage="active", expect=SERVED,
         why="reconcile's was-active escalation; an active-phase reason means it reached the room"),

    # ---- USER_STOPPED: the user said no. Final at EVERY stage --------------------------------
    # Founder ruling 2026-08-17: "explicit stop must be stop, evict."
    Cell("completed/stopped", status="completed", reason="stopped", expect=USER_STOPPED,
         why="the witnessed incident, exactly: stopped mid-meeting at 22:28:31, bot back at "
             "22:28:47"),
    Cell("failed/stopped@awaiting_admission", status="failed", reason="stopped",
         stage="awaiting_admission", stop_requested=True, expect=USER_STOPPED,
         why="stopped while WAITING to be admitted — the FSM's only legal pre-active terminal is "
             "`failed`, but the user said no just as loudly as one pressed mid-meeting"),
    Cell("failed/stopped@joining", status="failed", reason="stopped", stage="joining",
         stop_requested=True, expect=USER_STOPPED,
         why="stopped before it ever knocked; nothing here is a fault to retry"),
    Cell("stop_requested-flag-only", status="failed", reason="join_failure", stage="joining",
         stop_requested=True, expect=USER_STOPPED,
         why="the row's own terminal blamed the network, but the user had already pressed stop — "
             "user intent outranks the fault the race happened to record"),
]


# ---------------------------------------------------------------------------------------------
# The exhaustiveness gate — a new outcome cannot enter the enum without entering the table.
# ---------------------------------------------------------------------------------------------

def test_matrix_covers_every_reason_the_codebase_can_emit():
    """Add a CompletionReason (or another out-of-enum reason) and this fails until it has a row.

    Asserted against ``occurrence.KNOWN_REASONS`` (the sealed enum PLUS the out-of-enum values this
    codebase writes straight to ``data.completion_reason``) rather than against the enum alone —
    ``start_failed`` is exactly the value an enum-only check would have missed.
    """
    covered = {cell.reason for cell in MATRIX}
    missing = KNOWN_REASONS - covered
    assert not missing, (
        f"completion reasons with no matrix row: {sorted(missing)} — every terminal outcome needs "
        f"a decision (retry / served / user-stopped) and a test of its own"
    )
    assert None in covered, "the absent-reason cell is load-bearing — legacy rows carry no reason"


def test_known_reasons_covers_the_sealed_enum():
    """``KNOWN_REASONS`` must be a superset of the sealed enum: the gate above is only as complete
    as the set it checks against."""
    assert {reason.value for reason in CompletionReason} <= KNOWN_REASONS


def test_every_cell_has_a_distinct_name():
    names = [cell.name for cell in MATRIX]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------------------------
# One test per cell — the pure classification, then the live seam.
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell", MATRIX, ids=lambda c: c.name)
def test_cell_disposition(cell: Cell):
    """The classification itself: this terminal shape → this decision. (Cell rationale in ``why``.)"""
    row = {"id": 1, "status": cell.status, "data": cell.data(START.isoformat())}
    assert disposition(row) is cell.expect, cell.why


@pytest.mark.parametrize("cell", MATRIX, ids=lambda c: c.name)
async def test_cell_dispatch_through_the_live_seam(cell: Cell):
    """The consequence: after this terminal outcome, does a bot go back into the meeting?

    Drives the real path — calendar sync recreating (or not) the occurrence, then the auto-join
    sweep at +301s, which is past the 300s backoff and inside the 600s grace. That is the exact
    window in which the witnessed bot returned, so a cell that dispatches here is a cell that
    dispatches in production.
    """
    store, repo, runtime = _storm_rig()
    feed = _ics(_event(start="20260708T150000Z"))
    terminal_id = store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        status=cell.status, data=cell.data(START.isoformat()),
    )

    await sync_user(store, USER, parse_ics(feed, now=START + timedelta(seconds=10)))
    counters = await _sweep(repo, runtime, START + timedelta(seconds=301))

    recreated = [
        row for row in await store.list_meetings(USER)
        if row["id"] != terminal_id and row.get("status") not in ("completed", "failed")
    ]
    if cell.dispatches:
        assert recreated, f"{cell.name}: the occurrence is unserved and must re-arm — {cell.why}"
        assert counters["spawned"] == 1, f"{cell.name}: {cell.why}"
        assert len(runtime.specs) == 1
    else:
        assert not recreated, (
            f"{cell.name}: recreated a dispatchable row for a finished occurrence — {cell.why}"
        )
        assert counters["spawned"] == 0 and not runtime.specs, (
            f"{cell.name}: a bot went back into the meeting — {cell.why}"
        )


# ---------------------------------------------------------------------------------------------
# THE STOP-DOMINANCE COLUMN. Not a row in the table — a property OF the table.
#
# The matrix above asks "given how the run ended, may we go back?". This asks the orthogonal
# question the rev-193 storm answered wrong: "given that the USER STOPPED IT, does how the run
# happened to end matter at all?". It must not, for ANY cell — which is why this is a column
# applied to every row rather than three more rows chosen by whoever wrote them.
#
# The storm (2026-08-18, stage rev 193): a DELETE that lost a race left the row terminal as
# `join_failure` — a reason `retry.py` classes TRANSIENT — with `stop_requested` true the whole
# time. Classification alone was not enough: the reason must be dominated at the WRITE, because
# every reader that keys on `completion_reason` and not on the flag would otherwise re-dispatch.
# So the column is asserted in both places, the classifier and the writer.
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell", MATRIX, ids=lambda c: c.name)
def test_stop_dominance_classification(cell: Cell):
    """Add the user's stop to ANY terminal shape in the table and the verdict becomes USER_STOPPED.

    Including the SERVED cells: a bot that was evicted from the room, or rejected by the host, or
    ran out its own clock, is still a bot the user separately said no to — and neither verdict
    dispatches, so the stronger one is the honest record of why."""
    data = cell.data(START.isoformat())
    data["stop_requested"] = True
    row = {"id": 1, "status": cell.status, "data": data}

    assert disposition(row) is USER_STOPPED, (
        f"{cell.name}: `stop_requested` did not dominate — the row's own terminal reason "
        f"({cell.reason!r}) outranked the user's intent, which is the F3 defect"
    )
    assert not disposition(row).dispatchable


@pytest.mark.parametrize("cell", MATRIX, ids=lambda c: c.name)
def test_stop_dominance_at_the_writers(cell: Cell):
    """...and every writer of a terminal reason records `stopped`, not the cell's own reason.

    The classifier reads `data`; these produce it. A reason written as `join_failure` on a stopped
    row is re-dispatchable to every consumer that never looks at the flag — including
    ``retry.py``'s TRANSIENT/PERMANENT taxonomy, which is what actually let 26313 back in."""
    assert dominant_completion_reason(cell.reason, stop_requested=True) == "stopped"
    # ...while a run nobody stopped keeps its own reason, unchanged.
    assert dominant_completion_reason(cell.reason, stop_requested=False) == cell.reason


@pytest.mark.parametrize("cell", MATRIX, ids=lambda c: c.name)
async def test_stop_dominance_through_the_live_seam(cell: Cell):
    """The consequence, through the same real path every other cell is driven through: whatever
    this terminal shape is, once the user has stopped it NO bot goes back in.

    The SERVED and USER_STOPPED cells already assert this; running it for the RETRY cells too is
    the point — those are precisely the shapes that WOULD dispatch, so this proves the flag is what
    stops them rather than the outcome doing it anyway."""
    store, repo, runtime = _storm_rig()
    feed = _ics(_event(start="20260708T150000Z"))
    data = cell.data(START.isoformat())
    data["stop_requested"] = True
    store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        status=cell.status, data=data,
    )

    await sync_user(store, USER, parse_ics(feed, now=START + timedelta(seconds=10)))
    counters = await _sweep(repo, runtime, START + timedelta(seconds=301))

    assert counters["spawned"] == 0 and not runtime.specs, (
        f"{cell.name} + stop_requested: a bot went back into a meeting the user had stopped"
    )


# ---------------------------------------------------------------------------------------------
# Recurrence — the suppression is occurrence-scoped, and every cell must respect that.
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell", MATRIX, ids=lambda c: c.name)
async def test_cell_never_suppresses_the_next_occurrence(cell: Cell):
    """Whatever today's outcome was, NEXT WEEK's instance of the same UID arms normally.

    The one way this whole mechanism could fail catastrophically is by binding a decision to the
    calendar UID instead of the occurrence: a single stopped standup would then silence the series
    forever. Scoped by ``OCCURRENCE_MATCH_S``, and pinned here for every cell rather than for the
    one that happened to be written first.
    """
    store, _repo, _runtime = _storm_rig()
    weekly = _ics(_event(start="20260708T150000Z", rrule="FREQ=WEEKLY"))
    store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        status=cell.status, data=cell.data(START.isoformat()),
    )

    # Two hours on: today's occurrence is out of the window, so the feed's event for this UID is
    # next week's — a DIFFERENT occurrence, which today's outcome says nothing about.
    result = await sync_user(store, USER, parse_ics(weekly, now=START + timedelta(hours=2)))

    assert result["counts"]["created"] == 1, (
        f"{cell.name}: today's outcome suppressed NEXT WEEK's meeting — the decision is scoped to "
        f"the occurrence, never to the calendar UID"
    )
    fresh = next(row for row in await store.list_meetings(USER) if row["status"] == "scheduled")
    assert fresh["data"]["scheduled_at"] == "2026-07-15T15:00:00+00:00"
    assert fresh["data"].get("auto_join") is not False
    assert "stop_requested" not in fresh["data"]
