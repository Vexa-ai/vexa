"""P3a eval — lifecycle diagnostics (attributable reasons).

Replays a lifecycle.v1 event per terminal cause through the FSM (via the HTTP receiver) and
asserts the resulting meeting record + the emitted `meeting.status_change` webhook carry the
CORRECT attribution for each:

  * `completion_reason` / `failure_stage` (DERIVED SERVER-SIDE from the meeting's status at
    write-time, never the bot's stale payload),
  * `error_details`,
  * a `status_transition[]` trail (one entry per hop, each stamped with `transition_source`),
  * `bot_logs` / `bot_resources` forensics landed in `record.data`.

Each terminal-cause event is validated against the SEALED lifecycle.v1 schema before it is
replayed (the seam, P8), and each emitted webhook is the SEALED webhook.v1 `Envelope`. OFFLINE —
TestClient + in-memory store, no docker, no meeting, no network.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api.lifecycle import BotStatus, MeetingStore
from meeting_api.lifecycle.receiver import conforms, create_app


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

def _drive(client: TestClient, *events: dict) -> list:
    """POST a sequence of lifecycle.v1 events to the receiver; return the response bodies."""
    out = []
    for ev in events:
        r = client.post("/bots/internal/callback/lifecycle", json=ev)
        assert r.status_code == 200, r.text
        out.append(r.json())
    return out


JOINING = {"connection_id": "sess-uid", "container_id": "mtg-1-bot", "status": "joining"}
ACTIVE = {"connection_id": "sess-uid", "status": "active"}


def _client():
    deliveries: list = []
    app = create_app(store=MeetingStore(), on_status_change=lambda e: deliveries.append(e))
    return TestClient(app), app, deliveries


# ── the terminal-cause matrix (one event per terminal cause) ──────────────────────────────────────
# Each row: (label, prefix-events, terminal-event, expected reason, expected failure_stage).
# failure_stage is the EXPECTED SERVER-DERIVED value (from the stage we were in), which for the
# rejected/timeout cases deliberately DIFFERS from the payload's own failure_stage field.

_NORMAL_STOPPED = (
    "completed-stopped",
    [ACTIVE],
    {"connection_id": "sess-uid", "status": "completed", "exit_code": 0,
     "completion_reason": "stopped", "bot_logs": ["[ACT] leave received"]},
    "stopped", None,
)
_LEFT_ALONE = (
    "completed-left_alone",
    [ACTIVE],
    {"connection_id": "sess-uid", "status": "completed", "exit_code": 0,
     "completion_reason": "left_alone"},
    "left_alone", None,
)
_JOIN_FAILURE = (
    "failed-join_failure",
    [],  # straight from joining
    {"connection_id": "sess-uid", "status": "failed", "exit_code": 1,
     "completion_reason": "join_failure", "reason": "could not reach lobby"},
    "join_failure", "joining",  # was JOINING → stage joining (not the payload)
)
_ADMISSION_REJECTED = (
    "failed-admission_rejected",
    [{"connection_id": "sess-uid", "status": "awaiting_admission"}],
    {"connection_id": "sess-uid", "status": "failed", "exit_code": 1,
     "failure_stage": "active",  # STALE payload value — must be IGNORED
     "completion_reason": "awaiting_admission_rejected", "reason": "host denied admission"},
    "awaiting_admission_rejected", "awaiting_admission",  # server-derived, not "active"
)
_ADMISSION_TIMEOUT = (
    "failed-admission_timeout",
    [{"connection_id": "sess-uid", "status": "awaiting_admission"}],
    {"connection_id": "sess-uid", "status": "failed", "exit_code": 1,
     "completion_reason": "awaiting_admission_timeout", "reason": "no host"},
    "awaiting_admission_timeout", "awaiting_admission",
)
_MAX_BOT_TIME = (
    "failed-max_bot_time",
    [ACTIVE],
    {"connection_id": "sess-uid", "status": "failed", "exit_code": 137,
     "completion_reason": "max_bot_time_exceeded", "reason": "lifetime cap",
     "bot_resources": {"peak_memory_bytes": 512000000, "cpu_usage_usec": 9000000}},
    "max_bot_time_exceeded", "active",
)

_MATRIX = [
    _NORMAL_STOPPED, _LEFT_ALONE, _JOIN_FAILURE,
    _ADMISSION_REJECTED, _ADMISSION_TIMEOUT, _MAX_BOT_TIME,
]


@pytest.mark.parametrize("row", _MATRIX, ids=[r[0] for r in _MATRIX])
def test_terminal_cause_attribution(row):
    label, prefix, terminal, want_reason, want_stage = row
    # The terminal event itself conforms to the SEALED lifecycle.v1 schema (the seam).
    conforms(terminal, "LifecycleEvent")

    client, app, deliveries = _client()
    bodies = _drive(client, JOINING, *prefix, terminal)
    final = bodies[-1]

    # 1. The record carries the correct reason + SERVER-DERIVED failure_stage.
    assert final["completion_reason"] == want_reason
    assert final["failure_stage"] == want_stage
    terminal_status = "completed" if terminal["status"] == "completed" else "failed"
    assert final["meeting_status"] == terminal_status

    # 2. A failed exit derives error_details (from payload, or synthesized).
    if terminal_status == "failed":
        assert final["data"]["last_error"]["error_details"]

    # 3. The status_transition[] trail has one entry per hop, each with transition_source.
    trail = final["status_transition"]
    assert len(trail) == len(prefix) + 2  # joining + prefix + terminal
    assert trail[0] == {
        "from": None, "to": "joining",
        "timestamp": trail[0]["timestamp"], "source": "bot_callback",
    }
    last = trail[-1]
    assert last["to"] == terminal_status
    assert last["source"] == "bot_callback"
    assert last.get("completion_reason") == want_reason
    if want_stage is not None:
        assert last["failure_stage"] == want_stage

    # 4. Forensics landed in record.data when the terminal event carried them.
    if terminal.get("bot_logs"):
        assert final["data"]["bot_logs"] == terminal["bot_logs"]
    if terminal.get("bot_resources"):
        assert final["data"]["bot_resources"] == terminal["bot_resources"]

    # 5. The emitted meeting.status_change webhook carries the SAME attribution. One per hop.
    # (Typed events — meeting.started / meeting.completed / bot.failed — ride ALONGSIDE the
    # status_change stream; filter to status_change here, then assert the typed terminal below.)
    status_hooks = [d for d in deliveries if d["event_type"] == "meeting.status_change"]
    assert len(status_hooks) == len(prefix) + 2
    last_hook = status_hooks[-1]
    assert last_hook["event_type"] == "meeting.status_change"
    sc = last_hook["data"]["status_change"]
    assert sc["new_status"] == terminal_status
    assert sc["old_status"] == trail[-1]["from"]
    assert sc["transition_source"] == "bot_callback"
    assert last_hook["data"]["meeting"]["completion_reason"] == want_reason
    assert last_hook["data"]["meeting"]["failure_stage"] == want_stage

    # 6. The terminal transition ALSO emitted its typed event (webhook.v1 EventType parity):
    # completed → meeting.completed (post-meeting {meeting} envelope), failed → bot.failed.
    typed = [d for d in deliveries if d["event_type"] != "meeting.status_change"]
    want_terminal_event = "meeting.completed" if terminal_status == "completed" else "bot.failed"
    assert typed[-1]["event_type"] == want_terminal_event
    assert typed[-1]["data"]["meeting"]["completion_reason"] == want_reason
    if terminal_status == "completed":
        assert "status_change" not in typed[-1]["data"]


def test_failure_stage_is_server_derived_not_payload():
    """The canonical FM-003 assertion: the bot's stale `failure_stage` is IGNORED.

    The admission-rejected payload claims failure_stage="active", but the record was in
    `awaiting_admission` when it failed → the recorded stage is `awaiting_admission`.
    """
    client, app, deliveries = _client()
    bodies = _drive(
        client,
        JOINING,
        {"connection_id": "sess-uid", "status": "awaiting_admission"},
        {"connection_id": "sess-uid", "status": "failed", "exit_code": 1,
         "failure_stage": "active", "completion_reason": "awaiting_admission_rejected"},
    )
    assert bodies[-1]["failure_stage"] == "awaiting_admission"  # NOT "active"


def test_bot_logs_trimmed_oldest_first():
    """A bot_logs ring-buffer over the byte budget is trimmed from the OLDEST line."""
    # 60 KiB of 1 KiB lines (>50 KiB budget): the newest lines survive, the oldest are dropped.
    lines = [f"L{i:04d}:" + "x" * 1018 for i in range(60)]  # ~1 KiB each
    client, app, deliveries = _client()
    bodies = _drive(
        client, JOINING, ACTIVE,
        {"connection_id": "sess-uid", "status": "completed", "exit_code": 0,
         "completion_reason": "stopped", "bot_logs": lines},
    )
    kept = bodies[-1]["data"]["bot_logs"]
    assert bodies[-1]["data"]["bot_logs_truncated"] is True
    assert len(kept) < len(lines)
    assert kept[-1] == lines[-1]  # newest survives
    assert kept[0] != lines[0]    # oldest dropped


# ── the DEGRADED meeting: completed, but with no transcript and a reason why ────────────────────
# A backend that refuses every chunk used to produce a meeting indistinguishable from a silent
# room: the bot's faults were typed and attributed all the way to its composition root and then
# died in a console.error. The bot now counts them and reports once on the terminal event; this
# asserts the control plane PERSISTS that instead of accepting-and-dropping it (an additive field
# on lifecycle.v1, which is additionalProperties: true).

_STT_FAULT = {
    "kinds": [
        {"kind": "payment_required", "count": 18, "status": 402,
         "detail": "Insufficient balance. Available: 0.00 minutes",
         "first_at": "2026-07-19T12:00:00.000Z"},
    ],
    "total": 18,
}


def test_degraded_meeting_persists_why_the_transcript_is_empty():
    terminal = {
        "connection_id": "sess-uid", "status": "completed", "exit_code": 0,
        "completion_reason": "stopped",
        "reason": "stt_degraded: payment_required×18",
        "stt_fault": _STT_FAULT,
    }
    # The degraded terminal event is still a CONFORMING lifecycle.v1 event (the field is additive).
    conforms(terminal, "LifecycleEvent")

    client, app, deliveries = _client()
    final = _drive(client, JOINING, ACTIVE, terminal)[-1]

    # It completes normally — a dead STT degrades the VALUE, it does not fail the meeting.
    assert final["meeting_status"] == "completed"
    assert final["completion_reason"] == "stopped"

    # …and the meeting now says WHY it has no transcript, in the backend's own words.
    persisted = final["data"]["stt_fault"]
    assert persisted["total"] == 18
    assert persisted["kinds"][0]["kind"] == "payment_required"
    assert persisted["kinds"][0]["status"] == 402
    assert "Insufficient balance" in persisted["kinds"][0]["detail"]

    # The webhook carries the same attribution — an integrator sees it without polling.
    status_hooks = [d for d in deliveries if d["event_type"] == "meeting.status_change"]
    assert status_hooks[-1]["data"]["meeting"]["data"]["stt_fault"]["total"] == 18


def test_healthy_meeting_carries_no_stt_fault():
    """Negative control: the field appears ONLY when something actually degraded."""
    client, app, deliveries = _client()
    final = _drive(client, JOINING, ACTIVE, {
        "connection_id": "sess-uid", "status": "completed", "exit_code": 0,
        "completion_reason": "stopped",
    })[-1]
    assert "stt_fault" not in final["data"]
