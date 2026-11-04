"""A ``stopped`` nobody asked for, from a bot still joining or in the lobby, is a failure to join,
not the user's stop (the rule of Vexa-ai/vexa#1993's ``unasked_stop``, for these two stages).

A bot SIGTERM'd before it is admitted (Kubernetes preemption, an eviction, a node drain) aborts its
join, withdraws and reports ``failed`` / ``stopped`` with its own ``stopped_while_joining`` /
``user_action`` evidence: the bot cannot tell the signal from the user's Stop. The row can. The stop
route and the service-authority teardown both write ``data.stop_requested`` before the bot is told
to go; without it nobody in Vexa ended the run. Before this change such a run was recorded as the
user's stop, attributed ``user_action`` (outside the system-failure rate) and, through
``lifecycle.occurrence``, never re-dispatched.

Now, for a terminal the bot marks ``ended_by: termination_signal`` with no stop requested:
``completion_reason: join_failure`` (retryable), ``join_evidence`` derived server-side with
``attribution: system_fault`` (the bot's timings, node and screenshot kept), the stage as before.
A stop that WAS requested, and an unmarked ``stopped`` (a ``leave`` act; the tape library's
``user-stopped-in-the-lobby``), are untouched. Driven through the shipped callback route.
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


def _ends_in(stage: str, *, stop_requested: bool, marker: bool = True) -> dict:
    """The bot reports `joining` (and `awaiting_admission`), then its withdraw terminal."""
    async def scenario():
        repo = InMemoryMeetingRepo()
        m = await repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data={})
        await repo.create_session(meeting_id=m["id"], session_uid="sess-uid")
        await repo.set_bot_container(meeting_id=m["id"], bot_container_id="wl-1")
        repo.set_status(m["id"], "requested")
        app = create_app(open_callbacks=True, meeting_repo=repo)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            for st in ("joining", "awaiting_admission")[: 2 if stage == "awaiting_admission" else 1]:
                assert (await c.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": st})).status_code == 200
            if stop_requested:   # the stop route: the flag first, then the leave command / teardown
                repo._meetings[m["id"]]["data"]["stop_requested"] = True
            body = {"connection_id": "sess-uid", "status": "failed", "failure_stage": stage,
                    "completion_reason": "stopped", "exit_code": 0,
                    "reason": "stopped while awaiting admission (withdrew the join request)",
                    "join_evidence": {**BOT_EVIDENCE, "stage": stage}}
            if marker:
                body["ended_by"] = "termination_signal"
            r = await c.post(LIFECYCLE, json=body)
            assert r.status_code == 200, r.text
        return repo._meetings[m["id"]]
    return asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["joining", "awaiting_admission"])
def test_an_unasked_stop_before_admission_is_a_join_failure_and_ours(stage):
    row = _ends_in(stage, stop_requested=False)
    data = row["data"]
    assert row["status"] == "failed"
    assert data["failure_stage"] == stage
    assert data["completion_reason"] == "join_failure"            # retryable, not the user's stop
    ev = data["join_evidence"]
    assert ev["attribution"] == "system_fault"                      # not user_action
    assert ev["source"] == "derived" and ev["stage"] == stage
    assert "no stop was requested" in ev["detail"]
    # what the bot measured is kept
    assert ev["timings"] == BOT_EVIDENCE["timings"] and ev["node"] == BOT_EVIDENCE["node"]
    assert disposition(row) is Disposition.RETRY


@pytest.mark.parametrize("stage", ["joining", "awaiting_admission"])
@pytest.mark.parametrize("stop_requested,marker", [(True, True), (True, False), (False, False)])
def test_a_requested_or_unmarked_stop_before_admission_is_still_the_users(stage, stop_requested, marker):
    row = _ends_in(stage, stop_requested=stop_requested, marker=marker)
    data = row["data"]
    assert data["completion_reason"] == "stopped"
    assert data["join_evidence"]["reason"] == "stopped_while_joining"
    assert data["join_evidence"]["attribution"] == "user_action"
    assert disposition(row) is Disposition.USER_STOPPED
