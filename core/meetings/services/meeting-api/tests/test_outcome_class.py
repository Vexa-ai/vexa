"""outcome_class / outcome_reason — the customer-facing grading of how a bot run ended.

Pins three things:

1. The classifier is the canonical one: every row of the ported CASES table classifies as the
   business module says (same classes, same slugs, same rule order), plus one case per class.
2. The adapters: ``outcome_for_meeting`` reads the row the way meeting-api stores it
   (``segments_captured``, the ``status_transition`` trail, ``artifact_deletion``) and never raises.
3. The writers: a terminal through the lifecycle callback (``apply_change`` → the repo's
   ``update_meeting_status``) and the FSM-bypass ``fail_meeting`` both persist the two fields, and the
   #1655 shape (a Meet lobby wait written as ``join_failure`` at ``awaiting_admission``) lands as
   ``host_or_platform`` — "not admitted by host", not a failure of ours.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.outcome import (
    CASES,
    CLASSES,
    PENDING,
    Outcome,
    classify,
    outcome_for_meeting,
    stamp_outcome,
)

NOW = dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)
ENDPOINT = "/bots/internal/callback/lifecycle"


# ── 1. the ported table ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("row,want", CASES, ids=[f"{w[0]}:{w[1]}" for _, w in CASES])
def test_ported_cases(row, want):
    assert tuple(classify(row, now=NOW)) == want


@pytest.mark.parametrize(
    "row,want",
    [
        (dict(status="failed", completion_reason="join_failure", failure_stage="joining",
              detail="page.goto: Timeout 30000ms exceeded"), ("ours", "page_or_browser")),
        (dict(status="failed", completion_reason="awaiting_admission_timeout",
              failure_stage="awaiting_admission"), ("host_or_platform", "not_admitted")),
        (dict(status="failed", completion_reason="stopped", failure_stage="awaiting_admission"),
         ("user", "stopped_before_admission")),
        (dict(status="completed", completion_reason="left_alone", segments=120, active_minutes=40),
         ("normal", "delivered_left_alone")),
        (dict(status="completed", completion_reason="stopped"),
         ("unknown", "completed_no_evidence:stopped")),
        (dict(status="awaiting_admission", created_at="2026-10-20T23:00:00Z"),
         (PENDING, "awaiting_admission")),
        (dict(status="scheduled"), (PENDING, "scheduled")),
    ],
    ids=["ours", "host_or_platform", "user", "normal", "unknown", "pending", "pending_scheduled"],
)
def test_one_case_per_class(row, want):
    assert tuple(classify(row, now=NOW)) == want


def test_every_class_is_covered_by_the_table():
    assert {w[0] for _, w in CASES} >= set(CLASSES)


def test_only_ours_is_watched():
    assert Outcome("ours", "x").watched
    assert not any(Outcome(c, "x").watched for c in CLASSES if c != "ours")


# ── 2. the adapters ─────────────────────────────────────────────────────────────────────────────

def _trail(active_at: str = "2026-10-01T10:00:00Z") -> list:
    return [
        {"from": None, "to": "joining", "timestamp": "2026-10-01T09:59:30Z"},
        {"from": "joining", "to": "active", "timestamp": active_at},
    ]


def test_completed_zero_segments_reads_trail_and_end_time():
    """Active minutes run from the first `active` transition to end_time (not start_time)."""
    start = dt.datetime(2026, 10, 1, 10, 0)  # naive UTC, as the DB columns hold it
    base = {"completion_reason": "left_alone", "segments_captured": 0, "status_transition": _trail()}
    def grade(data, minutes):
        return tuple(outcome_for_meeting(status="completed", data=data,
                                         end_time=start + dt.timedelta(minutes=minutes),
                                         created_at="2026-10-01T09:59:00Z"))
    assert grade(base, 25) == ("normal", "no_speech_heard")
    assert grade({**base, "participants": ["a", "b"]}, 25) == ("ours", "empty_transcript")
    assert grade({**base, "completion_reason": "stopped"}, 25) == ("ours", "empty_transcript")
    assert grade(base, 1) == ("normal", "everyone_left_early")


def test_stopping_transition_counts_as_a_user_stop():
    data = {"completion_reason": "join_failure", "status_transition": [
        {"to": "joining", "timestamp": "2026-10-01T10:00:00Z"},
        {"to": "stopping", "timestamp": "2026-10-01T10:00:20Z"},
        {"to": "failed", "timestamp": "2026-10-01T10:00:25Z"},
    ]}
    o = outcome_for_meeting(status="failed", data=data, created_at="2026-10-01T09:59:00Z")
    assert tuple(o) == ("user", "stopped_before_admission")


def test_explicit_segment_count_overrides_data():
    data = {"completion_reason": "stopped", "segments_captured": 0}
    o = outcome_for_meeting(status="completed", data=data, segments=12)
    assert tuple(o) == ("normal", "delivered_stopped")


def test_artifact_deletion_reads_as_deleted_by_user():
    data = {"completion_reason": "stopped", "segments_captured": 0,
            "artifact_deletion": {"state": "complete"}, "status_transition": _trail()}
    o = outcome_for_meeting(status="completed", data=data,
                            end_time=dt.datetime(2026, 10, 1, 10, 20))
    assert tuple(o) == ("user", "deleted_by_user")


def test_detail_comes_from_failure_reason_and_join_evidence():
    o = outcome_for_meeting(
        status="failed",
        data={"completion_reason": "join_failure", "failure_stage": "joining",
              "join_evidence": {"detail": "teams_auth_redirect: redirected to sign-in"}},
    )
    assert tuple(o) == ("host_or_platform", "tenant_requires_sign_in")


def test_adapter_never_raises():
    o = outcome_for_meeting(status="completed", data={"segments_captured": "not-a-number"})
    assert tuple(o) == ("unknown", "classifier_error")


def test_stamp_is_terminal_only():
    assert stamp_outcome({}, status="active") == {}
    d = stamp_outcome({"completion_reason": "validation_error"}, status="failed")
    assert (d["outcome_class"], d["outcome_reason"]) == ("user", "malformed_request")


# ── 3. the writers ──────────────────────────────────────────────────────────────────────────────

def _seed(repo: InMemoryMeetingRepo, status: str = "requested") -> dict:
    m = asyncio.run(repo.create_meeting(user_id=1, platform="google_meet",
                                        native_meeting_id="abc-defg-hij", data={}))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid="sess-uid"))
    repo.set_status(m["id"], status)
    return m


def _post(client, event):
    r = client.post(ENDPOINT, json=event)
    assert r.status_code == 200, r.text
    return r


def test_lifecycle_callback_persists_outcome_for_1655_lobby_wait(goldens):
    """#1655: the bot knocked, waited in the Meet lobby and was never admitted, but reported
    join_failure. The persisted row says host_or_platform, not a fault of ours."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    client = TestClient(create_app(open_callbacks=True, meeting_repo=repo))
    _post(client, goldens["joining"])
    _post(client, {"connection_id": "sess-uid", "status": "awaiting_admission",
                   "timestamp": "2026-10-20T10:00:05.000Z"})
    _post(client, {"connection_id": "sess-uid", "status": "failed", "exit_code": 1,
                   "timestamp": "2026-10-20T10:13:00.000Z",
                   "completion_reason": "join_failure",
                   "reason": "Bot failed to join the Google Meet meeting — no meeting indicators "
                             "found within timeout"})

    data = repo._meetings[m["id"]]["data"]
    assert data["failure_stage"] == "awaiting_admission"
    assert (data["outcome_class"], data["outcome_reason"]) == ("host_or_platform", "not_admitted_1655")


def test_lifecycle_callback_user_stop_in_lobby_is_user(goldens):
    """A stop pressed while the bot waits in the lobby: the trail carries `stopping`, so the run
    is the user's, whatever the bot reported."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    client = TestClient(create_app(open_callbacks=True, meeting_repo=repo))
    _post(client, goldens["joining"])
    _post(client, {"connection_id": "sess-uid", "status": "awaiting_admission",
                   "timestamp": "2026-10-20T10:00:05.000Z"})
    repo._meetings[m["id"]]["data"]["stop_requested"] = True
    _post(client, {"connection_id": "sess-uid", "status": "failed", "exit_code": 0,
                   "timestamp": "2026-10-20T10:01:00.000Z", "completion_reason": "join_failure",
                   "reason": "self_initiated_leave"})
    data = repo._meetings[m["id"]]["data"]
    assert data["outcome_class"] == "user"


def test_lifecycle_callback_completed_uses_segment_count(goldens):
    """A completed run is graded at the repo write, where segments_captured is known."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, "active")
    repo._meetings[m["id"]]["data"]["segments_captured"] = 40
    client = TestClient(create_app(open_callbacks=True, meeting_repo=repo))
    _post(client, goldens["completed-stopped"])

    data = repo._meetings[m["id"]]["data"]
    assert (data["outcome_class"], data["outcome_reason"]) == ("normal", "delivered_stopped")


def test_get_meetings_hoists_outcome_fields():
    """GET /meetings carries outcome_class/outcome_reason at top level, like completion_reason."""
    from meeting_api.collector import create_app as create_collector_app
    from meeting_api.collector.fakes import InMemoryTranscriptStore

    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij",
                       status="failed",
                       data={"completion_reason": "join_failure", "failure_stage": "awaiting_admission",
                             "outcome_class": "host_or_platform", "outcome_reason": "not_admitted_1655"})
    client = TestClient(create_collector_app(store, redis=None))
    r = client.get("/meetings", headers={"x-user-id": "7"})
    assert r.status_code == 200, r.text
    (m,) = r.json()["meetings"]
    assert (m["outcome_class"], m["outcome_reason"]) == ("host_or_platform", "not_admitted_1655")


def test_fail_meeting_persists_outcome():
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    row = asyncio.run(repo.fail_meeting(
        meeting_id=m["id"], reason="workload dead on spawn: OOMKilled",
    ))
    assert row["status"] == "failed"
    assert (row["data"]["outcome_class"], row["data"]["outcome_reason"]) == ("ours", "bot_start_failed")


def test_fail_meeting_user_stop_is_user():
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    repo._meetings[m["id"]]["data"]["stop_requested"] = True
    row = asyncio.run(repo.fail_meeting(meeting_id=m["id"], reason="stopped before spawn",
                                        failure_stage=None))
    assert row["data"]["completion_reason"] == "stopped"
    assert row["data"]["outcome_class"] == "user"


# ── 4. the one-off backfill (pure planning; never run against a DB here) ────────────────────────

def _backfill():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "backfill_outcome_class.py"
    spec = importlib.util.spec_from_file_location("backfill_outcome_class", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_plans_only_absent_terminal_rows():
    bf = _backfill()
    start = dt.datetime(2026, 10, 1, 10, 0)
    rows = [
        # legacy #1655 row, never graded → write host_or_platform
        {"id": 1, "status": "failed", "data": {"completion_reason": "join_failure",
                                               "failure_stage": "awaiting_admission"}},
        # already graded live → never overwritten
        {"id": 2, "status": "failed", "data": {"completion_reason": "join_failure",
                                               "failure_stage": "awaiting_admission",
                                               "outcome_class": "host_or_platform"}},
        # completed, no segments_captured key → the live transcription count is the evidence
        {"id": 3, "status": "completed", "data": {"completion_reason": "stopped"},
         "live_segments": 9, "start_time": start, "end_time": start + dt.timedelta(minutes=30)},
        # still in flight → counted as pending, never written
        {"id": 4, "status": "active", "data": {}, "created_at": "2026-10-21T00:00:00Z"},
    ]
    plans = [bf.plan_row(r, now=NOW) for r in rows]
    assert [tuple(o) for o, _ in plans] == [
        ("host_or_platform", "not_admitted_1655"),
        ("host_or_platform", "not_admitted_1655"),
        ("normal", "delivered_stopped"),
        (PENDING, "active"),
    ]
    assert [w for _, w in plans] == [True, False, True, False]
    s = bf.summarize_plan(plans)
    assert s["writes"] == 2 and s["by_class"]["host_or_platform"] == 2


def test_lifecycle_callback_reasonless_lobby_failure_is_not_ours(goldens):
    """The FSM synthesizes error_details 'Bot exited with code 1; reason: None' when the bot sends
    no reason. A lobby wait that ends that way is still the host not admitting the bot."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    client = TestClient(create_app(open_callbacks=True, meeting_repo=repo))
    _post(client, goldens["joining"])
    _post(client, {"connection_id": "sess-uid", "status": "awaiting_admission",
                   "timestamp": "2026-10-20T10:00:05.000Z"})
    _post(client, {"connection_id": "sess-uid", "status": "failed", "exit_code": 1,
                   "timestamp": "2026-10-20T10:13:00.000Z", "completion_reason": "join_failure"})
    data = repo._meetings[m["id"]]["data"]
    assert data["last_error"]["error_details"] == "Bot exited with code 1; reason: None"
    # The FSM's own join_evidence (it saw awaiting_admission) gives the precise slug.
    assert data["join_evidence"]["reason"] == "awaiting_admission_timeout"
    assert (data["outcome_class"], data["outcome_reason"]) == ("host_or_platform", "not_admitted")
