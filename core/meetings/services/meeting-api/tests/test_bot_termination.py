"""A bot ended under a live meeting by the cluster is recorded as that, not as a user stop or as
"left alone" (lifecycle/bot_termination.py).

The class: a RUNNING bot killed by Kubernetes. Preempted for a higher-priority system Pod (the
scal-1104 stand, 4 Nov 2026: a coredns replica preempted bot r3c-fix-4-05), evicted, or OOM-killed
at its memory limit. Before this change:

  * SIGTERM → the bot's own ``completed`` / ``stopped`` was applied as sent: the row read as a USER
    stop (``lifecycle.occurrence`` → USER_STOPPED). The bot now marks a run a termination signal
    ended (``ended_by: "termination_signal"``); with no stop requested on the row, that is the case;
  * SIGKILL → the runtime-confirmed synthetic terminal wrote ``completed`` / ``left_alone``.

After: the status is what it was (the bot served until it was ended, the service record is unchanged),
the false reason is not written, and ``data.bot_termination`` names the cause with
``attribution: system_fault``. Every case without that evidence is pinned unchanged here too.

Driven through the shipped routes over in-process ASGI (``/bots/internal/callback/lifecycle``,
``/runtime/callback``) and the shipped reconcile sweep.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.lifecycle.occurrence import Disposition, disposition
from meeting_api.lifecycle.reconcile import reconcile_stale_nonterminal_sweep

LIFECYCLE = "/bots/internal/callback/lifecycle"
RUNTIME = "/runtime/callback"
JOINED = (("joining", "2026-11-04T09:00:00.000Z"), ("active", "2026-11-04T09:00:20.000Z"))


async def _seed(repo, *, platform="google_meet", session_uid="sess-uid", workload="wl-1"):
    m = await repo.create_meeting(user_id=1, platform=platform, native_meeting_id="m1", data={})
    await repo.create_session(meeting_id=m["id"], session_uid=session_uid)
    await repo.set_bot_container(meeting_id=m["id"], bot_container_id=workload)
    repo.set_status(m["id"], "requested")
    repo._meetings[m["id"]]["data"]["transcription_provider"] = "none"
    return m


def _asgi(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tsrv")


async def _join(c):
    for st, ts in JOINED:
        r = await c.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": st, "timestamp": ts})
        assert r.status_code == 200, r.text


SIGNAL = {"ended_by": "termination_signal"}


def _bot_ends(*, platform="google_meet", stop_requested=False, marker=SIGNAL):
    """The bot joins, then reports its own ``completed`` / ``stopped``: what its SIGTERM handler sends
    (with the marker) and what a ``leave`` act produces (without)."""
    async def scenario():
        repo = InMemoryMeetingRepo()
        m = await _seed(repo, platform=platform)
        app = create_app(open_callbacks=True, meeting_repo=repo)
        async with _asgi(app) as c:
            await _join(c)
            if stop_requested:   # the stop route: flag first, status stopping, then the leave command
                repo._meetings[m["id"]]["data"]["stop_requested"] = True
                repo.set_status(m["id"], "stopping")
            r = await c.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": "completed",
                                              "completion_reason": "stopped", "exit_code": 0,
                                              "timestamp": "2026-11-04T09:02:22.000Z", **marker})
            assert r.status_code == 200, r.text
        return repo._meetings[m["id"]]
    return asyncio.run(scenario())


def test_a_bot_terminated_with_no_stop_requested_is_named_not_a_user_stop():
    row = _bot_ends()
    data = row["data"]
    assert row["status"] == "completed"
    assert data.get("completion_reason") is None                      # not the user's "stopped"
    assert data["bot_termination"] == {
        "cause": "terminated_externally", "attribution": "system_fault", "source": "bot_callback",
        "detail": data["reason"]}
    assert "no stop was requested" in data["reason"]
    assert disposition(row) is Disposition.SERVED                       # not USER_STOPPED
    # the service record the meter rates from is the same as for any served visit
    assert data["service_provenance"]["bot_outcome"] == "served"
    assert data["service_provenance"]["bot_departed_at"] == "2026-11-04T09:02:22.000Z"


def test_a_user_stop_is_still_a_user_stop():
    """The runtime's own SIGTERM after a user stop carries the marker too: the flag decides."""
    row = _bot_ends(stop_requested=True)
    assert row["status"] == "completed"
    assert row["data"]["completion_reason"] == "stopped"
    assert "bot_termination" not in row["data"]
    assert disposition(row) is Disposition.USER_STOPPED


@pytest.mark.parametrize("platform", ["google_meet", "voice_note"])
def test_a_stopped_without_the_bots_marker_is_applied_as_sent(platform):
    """A ``leave`` act, a voice note whose audio ended, a bot from before the marker: unchanged."""
    row = _bot_ends(platform=platform, marker={})
    assert row["data"]["completion_reason"] == "stopped"
    assert "bot_termination" not in row["data"]


def _runtime_says(body: dict):
    """The bot joins; then, with no report of its own, the runtime posts its workload's end."""
    async def scenario():
        repo = InMemoryMeetingRepo()
        m = await _seed(repo)
        app = create_app(open_callbacks=True, meeting_repo=repo)
        async with _asgi(app) as c:
            await _join(c)
            r = await c.post(RUNTIME, json={"workloadId": "wl-1", "at": "2026-11-04T09:05:00.000Z", **body})
            assert r.status_code == 200, r.text
        return repo._meetings[m["id"]]
    return asyncio.run(scenario())


@pytest.mark.parametrize("stop_reason,cause", [("oom", "oom_killed"), ("failed", "killed")])
def test_a_bot_killed_under_the_meeting_is_named_not_left_alone(stop_reason, cause):
    row = _runtime_says({"state": "stopped", "exitCode": 137, "stopReason": stop_reason})
    data = row["data"]
    assert row["status"] == "completed"
    assert data.get("completion_reason") is None                      # not "left_alone"
    assert data["bot_termination"]["cause"] == cause
    assert data["bot_termination"]["attribution"] == "system_fault"
    assert data["bot_termination"]["source"] == "runtime"
    assert "exitCode=137" in data["reason"] and f"stopReason={stop_reason}" in data["reason"]
    assert data["service_provenance"]["bot_outcome"] == "served"


@pytest.mark.parametrize("body", [
    {"state": "destroyed"},                                            # no exit evidence at all
    {"state": "stopped", "exitCode": 0, "stopReason": "completed"},    # a gone Pod reads exit 0
])
def test_a_workload_end_with_no_kill_evidence_is_unchanged(body):
    row = _runtime_says(body)
    assert row["data"]["completion_reason"] == "left_alone"
    assert "bot_termination" not in row["data"]


def test_the_reconcile_sweep_names_a_killed_bot_too():
    """The backstop path: an `active` row gone quiet past the grace, whose workload the runtime
    reports OOM-killed, converges with the same named block (not `left_alone`)."""
    async def scenario():
        repo = InMemoryMeetingRepo()
        m = await _seed(repo)
        app = create_app(open_callbacks=True, meeting_repo=repo)
        runtime = FakeRuntimeClient(workloads={"wl-1": {
            "workloadId": "wl-1", "state": "stopped", "exitCode": 137, "stopReason": "oom"}})
        async with _asgi(app) as c:
            await _join(c)
            repo._meetings[m["id"]]["updated_at"] = "2026-11-04T08:00:00+00:00"   # quiet past the grace

            async def post(body):
                return (await c.post(LIFECYCLE, json=body)).status_code

            n = await reconcile_stale_nonterminal_sweep(
                repo, runtime, post, stop_grace=45.0, active_grace=300.0,
                log=logging.getLogger("test.bot_termination"))
        return n, repo._meetings[m["id"]]

    n, row = asyncio.run(scenario())
    assert n == 1
    assert row["status"] == "completed"
    assert row["data"].get("completion_reason") is None
    assert row["data"]["bot_termination"]["cause"] == "oom_killed"


def test_the_block_is_sanitized():
    from meeting_api.lifecycle import bot_termination

    assert bot_termination.sanitize({"cause": "preempted", "attribution": "system_fault",
                                     "source": "runtime"}) is None          # not a known cause
    assert bot_termination.sanitize({"cause": "killed", "attribution": "host_action",
                                     "source": "runtime"}) is None
    long = bot_termination.sanitize({"cause": "killed", "attribution": "system_fault",
                                     "source": "runtime", "detail": "x" * 1000})
    assert len(long["detail"]) == bot_termination.DETAIL_MAX_CHARS
