"""A ``stopped`` nobody asked for, before the bot was admitted, is a failure to join, not the user's
stop: Vexa-ai/vexa#1993's ``unasked_stop``, widened from the first event to every pre-active stage.

A bot SIGTERM'd before admission (Kubernetes preemption, an eviction, a node drain) aborts its join,
withdraws and reports ``failed`` / ``stopped`` with its own ``stopped_while_joining`` / ``user_action``
evidence: it cannot tell the signal from the user's Stop. The row can. The stop route and the
service-authority teardown write ``data.stop_requested`` before the bot is told to go; without it
nobody in Vexa ended the run. Before this change the run was recorded as the user's stop,
``user_action`` (outside the system-failure rate), and ``lifecycle.occurrence`` never re-dispatched it.

Now the FSM (``machine._terminal_reason``, the one decider) records ``join_failure`` (retryable) and
derives ``join_evidence`` itself: ``attribution: system_fault``, the bot's timings, node and screenshot
kept, its verdict dropped. A requested stop is untouched, and so is every ``stopped`` once the bot
was ``active`` (that stage waits for the founder's choice of a reason). Driven through the shipped
callback route over in-process ASGI.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.occurrence import Disposition, disposition

LIFECYCLE = "/bots/internal/callback/lifecycle"
BOT_EVIDENCE = {"reason": "stopped_while_joining", "attribution": "user_action", "source": "bot",
                "detail": "stopped while awaiting admission (withdrew the join request)",
                "timings": {"time_to_lobby_ms": 19000, "time_in_lobby_ms": 41000, "total_ms": 60000},
                "lobby_budget_ms": 900000, "node": "lke590708-865130-jt6t7"}
WALK = {"joining": ("joining",), "awaiting_admission": ("joining", "awaiting_admission"),
        "needs_help": ("joining", "awaiting_admission", "needs_help"), "active": ("joining", "active")}


def _ends_after(walk: str, *, stop_requested: bool) -> dict:
    """The bot reports the statuses of ``walk``, then its own ``stopped`` terminal."""
    async def scenario():
        repo = InMemoryMeetingRepo()
        m = await repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data={})
        await repo.create_session(meeting_id=m["id"], session_uid="sess-uid")
        await repo.set_bot_container(meeting_id=m["id"], bot_container_id="wl-1")
        repo.set_status(m["id"], "requested")
        app = create_app(open_callbacks=True, meeting_repo=repo)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            for st in WALK[walk]:
                assert (await c.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": st})).status_code == 200
            if stop_requested:   # the stop route: the flag first, then the leave command / teardown
                repo._meetings[m["id"]]["data"]["stop_requested"] = True
            if walk == "active":
                body = {"connection_id": "sess-uid", "status": "completed", "completion_reason": "stopped",
                        "exit_code": 0}
            else:
                stage = "awaiting_admission" if walk != "joining" else "joining"
                body = {"connection_id": "sess-uid", "status": "failed", "failure_stage": stage,
                        "completion_reason": "stopped", "exit_code": 0,
                        "reason": "stopped while awaiting admission (withdrew the join request)",
                        "join_evidence": {**BOT_EVIDENCE, "stage": stage}}
            r = await c.post(LIFECYCLE, json=body)
            assert r.status_code == 200, r.text
        return repo._meetings[m["id"]]
    return asyncio.run(scenario())


@pytest.mark.parametrize("walk,stage", [("joining", "joining"), ("awaiting_admission", "awaiting_admission"),
                                        ("needs_help", "awaiting_admission")])
def test_an_unasked_stop_before_admission_is_a_join_failure_and_ours(walk, stage):
    row = _ends_after(walk, stop_requested=False)
    data = row["data"]
    assert row["status"] == "failed"
    assert data["failure_stage"] == stage
    assert data["completion_reason"] == "join_failure"            # retryable, not the user's stop
    ev = data["join_evidence"]
    assert ev["attribution"] == "system_fault"                      # not the bot's user_action
    assert ev["source"] == "derived" and ev["stage"] == stage
    assert "no stop was requested" in ev["detail"]
    assert ev["timings"] == BOT_EVIDENCE["timings"] and ev["node"] == BOT_EVIDENCE["node"]
    assert disposition(row) is Disposition.RETRY


@pytest.mark.parametrize("walk", ["joining", "awaiting_admission", "needs_help"])
def test_a_requested_stop_before_admission_is_still_the_users(walk):
    row = _ends_after(walk, stop_requested=True)
    data = row["data"]
    assert data["completion_reason"] == "stopped"
    assert data["join_evidence"]["reason"] == "stopped_while_joining"
    assert data["join_evidence"]["attribution"] == "user_action"
    assert disposition(row) is Disposition.USER_STOPPED


def test_an_active_bots_stopped_is_left_as_it_is():
    """The active stage is not this rule's: it waits for a reason value (a contract decision)."""
    row = _ends_after("active", stop_requested=False)
    assert row["status"] == "completed" and row["data"]["completion_reason"] == "stopped"


@pytest.mark.parametrize("reason,stop_requested,pre_active,want", [
    ("stopped", False, True, True),
    ("stopped", True, True, False),            # the user asked
    ("stopped", False, False, False),          # admitted: not this rule's stage
    ("join_failure", False, True, False),      # only the user-stop reason is in question
    (None, False, True, False),
])
def test_the_rule_itself(reason, stop_requested, pre_active, want):
    from meeting_api.lifecycle.machine import unasked_stop

    assert unasked_stop(reason, stop_requested=stop_requested, pre_active=pre_active) is want
