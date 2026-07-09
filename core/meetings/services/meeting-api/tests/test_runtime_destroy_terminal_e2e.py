"""END-TO-END coverage for the live reaper-loop bug: a runtime-confirmed workload destroy must
advance a still-non-terminal meeting to a terminal state THROUGH THE REAL ``POST /runtime/callback``
handler — no mock of ``drive_terminal`` — so the actual FSM transition the production path hits is
exercised, and the meeting reaches terminal, the reaper stops re-issuing, and the copilot
``session_end`` reap fires.

Why a NEW file (and why ASGI, not the fake ``drive_terminal``): #64's runtime-callback tests injected
``client.post`` as the ``drive_terminal`` callable and drove ``synthesize_terminal_for_dead_workload``
directly — the REAL ``/runtime/callback`` handler (which builds its OWN ``_drive_terminal`` and, on the
merged code, POSTed to ``http://127.0.0.1:PORT``) was never executed, so the self-POST 409 / the
unreachable-loopback drop was invisible. These tests drive the shipped ``/runtime/callback`` route over
an in-process ASGI transport (``httpx.ASGITransport`` against the real app), so the handler's own
terminal-advance path runs end to end against the real FSM.

The load-bearing regression (RED on the pre-fix merged code): a bot that reported ONLY ``joining``
(in-process FSM record = JOINING) is user-stopped (DB = ``stopping``) and SIGKILLed before it could
report ``active``; the runtime posts ``state=destroyed``. The synthetic ``completed`` is
``joining → completed`` — illegal for a bot-driven edge — so the old self-POST 409'd, the meeting stayed
``stopping``, and the stop-reconcile sweep re-DELETEd (now 404) every tick forever. Post-fix the
runtime-destroy source forces the terminal edge in-process → ``completed``, and the row leaves the
stale-stopping listing.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.machine import (
    BotStatus,
    IllegalTransition,
    LifecycleSink,
    MeetingStore,
    TransitionSource,
)

LIFECYCLE = "/bots/internal/callback/lifecycle"
RUNTIME = "/runtime/callback"


class _StreamRedis:
    """Records publishes AND xadds (the copilot-reap path uses xadd)."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self.streams: dict[str, list[dict]] = {}

    async def publish(self, channel: str, data: str):
        self.published.append((channel, data))
        return 1

    async def xadd(self, stream: str, payload: dict):
        self.streams.setdefault(stream, []).append(payload)
        return f"{len(self.streams[stream])}-0"


class _ReaperRepo(InMemoryMeetingRepo):
    """InMemoryMeetingRepo + the ``list_stale_stopping`` the reaper loop lists from — so a test can
    assert a stuck ``stopping`` row IS listed (the reaper would re-issue) and, post-advance, is NOT."""

    def list_stale_stopping_sync(self) -> list[tuple[int, str, object]]:
        out: dict[int, tuple] = {}
        for s in reversed(self.sessions):
            mid = s["meeting_id"]
            row = self._meetings.get(mid)
            if row is None or row["status"] != "stopping":
                continue
            if mid not in out:
                out[mid] = (s["session_uid"], row.get("bot_container_id"))
        return [(mid, sid, bcid) for mid, (sid, bcid) in out.items()]

    async def list_stale_stopping(self, *, older_than_seconds: float):
        return self.list_stale_stopping_sync()


async def _seed(repo, *, status="requested", session_uid="sess-uid", workload="wl-1"):
    """Create a meeting + session + workload id at ``status`` (default ``requested`` — the fresh
    pre-callback shape; the bot's lifecycle callbacks then advance both the store and the DB)."""
    m = await repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data={})
    await repo.create_session(meeting_id=m["id"], session_uid=session_uid)
    await repo.set_bot_container(meeting_id=m["id"], bot_container_id=workload)
    repo.set_status(m["id"], status)
    return m


def _asgi(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tsrv")


# ── END-TO-END: the real /runtime/callback handler advances the meeting (no mocked drive_terminal) ──


def test_runtime_destroy_completes_stopping_after_only_joining_e2e():
    """THE LIVE BUG. Bot reported ONLY ``joining`` (FSM record = JOINING); user stop moved the DB to
    ``stopping``; the bot was killed before ``active``; the runtime posts ``destroyed``. Driven THROUGH
    the real ``POST /runtime/callback`` handler, the meeting must reach ``completed`` (reason ``stopped``)
    and leave the stale-stopping listing — NOT 409 and stay ``stopping`` (the reaper loop). On the
    pre-fix code the handler's self-POST 409'd (``joining → completed``) and this row stayed ``stopping``.
    """
    async def scenario():
        repo = _ReaperRepo()
        m = await _seed(repo)   # requested; the joining callback then sets store = JOINING, DB = joining
        redis = _StreamRedis()
        app = create_app(meeting_repo=repo, redis=redis)
        async with _asgi(app) as c:
            # The bot's only lifecycle event lands: joining. In-process FSM record → JOINING.
            r = await c.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": "joining"})
            assert r.status_code == 200, r.text
            # The user stops the still-joining bot: the DB row moves to `stopping` (server-side state).
            repo.set_status(m["id"], "stopping")
            # The reaper WOULD list this row while it is `stopping`.
            assert repo.list_stale_stopping_sync() == [(m["id"], "sess-uid", "wl-1")]

            # The runtime confirms the workload destroyed → the REAL handler drives the synthetic terminal.
            rc = await c.post(RUNTIME, json={"workloadId": "wl-1", "state": "destroyed"})
            assert rc.status_code == 200, rc.text
        return repo, redis, m

    repo, redis, m = asyncio.run(scenario())
    # (b) the meeting reached terminal …
    assert repo._meetings[m["id"]]["status"] == "completed", "still stuck non-terminal — the live bug"
    assert repo._meetings[m["id"]]["data"].get("completion_reason") == "stopped"
    # (c) … so the reaper no longer re-issues (the row left the stale-stopping listing).
    assert repo.list_stale_stopping_sync() == []
    # (d) session_end emitted on the ROW-keyed copilot feed → the copilot worker reaps.
    stream = f"tc:meeting:{m['id']}"
    markers = [p for p in redis.streams.get(stream, []) if p.get("type") == "session_end"]
    assert len(markers) == 1, f"no copilot reap on {stream}; streams={list(redis.streams)}"


def test_runtime_destroy_completes_stopping_after_active_e2e():
    """The canonical stop path: bot went active, user stop → ``stopping``, bot SIGKILLed at teardown
    before its own ``completed`` — runtime posts ``destroyed``. Through the real handler → ``completed``
    (reason ``stopped``), reaper listing empties, copilot reaped."""
    async def scenario():
        repo = _ReaperRepo()
        m = await _seed(repo)  # requested → the callbacks walk it up to active
        redis = _StreamRedis()
        app = create_app(meeting_repo=repo, redis=redis)
        async with _asgi(app) as c:
            for st in ("joining", "active"):
                assert (await c.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": st})).status_code == 200
            repo.set_status(m["id"], "stopping")
            rc = await c.post(RUNTIME, json={"workloadId": "wl-1", "state": "destroyed"})
            assert rc.status_code == 200, rc.text
        return repo, redis, m

    repo, redis, m = asyncio.run(scenario())
    assert repo._meetings[m["id"]]["status"] == "completed"
    assert repo._meetings[m["id"]]["data"].get("completion_reason") == "stopped"
    assert repo.list_stale_stopping_sync() == []
    assert len(redis.streams.get(f"tc:meeting:{m['id']}", [])) == 1


def test_runtime_destroy_fails_pre_active_e2e():
    """PRE-ACTIVE → failed, end to end. A meeting stuck ``awaiting_admission`` (bot killed in the waiting
    room before it could report ``active``) whose workload the runtime confirms ``destroyed`` reaches
    ``failed`` (attributed to the stage it died in), and still reaps the copilot."""
    async def scenario():
        repo = _ReaperRepo()
        m = await _seed(repo)  # requested → callbacks walk it to awaiting_admission
        redis = _StreamRedis()
        app = create_app(meeting_repo=repo, redis=redis)
        async with _asgi(app) as c:
            for st in ("joining", "awaiting_admission"):
                assert (await c.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": st})).status_code == 200
            rc = await c.post(RUNTIME, json={"workloadId": "wl-1", "state": "destroyed"})
            assert rc.status_code == 200, rc.text
        return repo, redis, m

    repo, redis, m = asyncio.run(scenario())
    assert repo._meetings[m["id"]]["status"] == "failed"
    assert repo._meetings[m["id"]]["data"].get("failure_stage") == "awaiting_admission"
    assert len(redis.streams.get(f"tc:meeting:{m['id']}", [])) == 1


def test_runtime_destroy_noop_on_already_terminal_e2e():
    """A normal teardown posts ``destroyed`` AFTER the bot's own ``completed`` already landed — the real
    handler must be a no-op (never re-open a terminal meeting, never double-reap)."""
    async def scenario():
        repo = _ReaperRepo()
        m = await _seed(repo)
        redis = _StreamRedis()
        app = create_app(meeting_repo=repo, redis=redis)
        async with _asgi(app) as c:
            for st in ("joining", "active", "completed"):
                ev = {"connection_id": "sess-uid", "status": st}
                if st == "completed":
                    ev["completion_reason"] = "left_alone"
                assert (await c.post(LIFECYCLE, json=ev)).status_code == 200
            rc = await c.post(RUNTIME, json={"workloadId": "wl-1", "state": "destroyed"})
            assert rc.status_code == 200, rc.text
        return repo, redis, m

    repo, redis, m = asyncio.run(scenario())
    assert repo._meetings[m["id"]]["status"] == "completed"
    assert repo._meetings[m["id"]]["data"].get("completion_reason") == "left_alone"
    # Exactly ONE session_end (the bot's own completed), not a second from the trailing destroy.
    assert len(redis.streams.get(f"tc:meeting:{m['id']}", [])) == 1


def test_runtime_nonterminal_state_does_not_advance_e2e():
    """A non-terminal runtime state (``running``) is not evidence — the real handler leaves the meeting
    untouched (no forced terminal off a live workload)."""
    async def scenario():
        repo = _ReaperRepo()
        m = await _seed(repo)
        app = create_app(meeting_repo=repo)
        async with _asgi(app) as c:
            for st in ("joining", "active"):
                assert (await c.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": st})).status_code == 200
            repo.set_status(m["id"], "stopping")
            assert (await c.post(RUNTIME, json={"workloadId": "wl-1", "state": "running"})).status_code == 200
        return repo, m

    repo, m = asyncio.run(scenario())
    assert repo._meetings[m["id"]]["status"] == "stopping"


# ── PURE FSM UNIT: runtime-destroy forces the terminal edge; illegal edges stay illegal ─────────────


@pytest.mark.parametrize(
    "stale,to,reason",
    [
        (BotStatus.JOINING, BotStatus.COMPLETED, "was-active stop killed a still-joining bot"),
        (BotStatus.JOINING, BotStatus.FAILED, "pre-active never reported"),
        (BotStatus.AWAITING_ADMISSION, BotStatus.COMPLETED, "stopped in the waiting room"),
        (BotStatus.NEEDS_HELP, BotStatus.COMPLETED, "escalated then destroyed"),
        (BotStatus.ACTIVE, BotStatus.COMPLETED, "canonical stop"),
    ],
)
def test_force_terminal_on_destroy_is_legal_from_any_nonterminal(stale, to, reason):
    """The runtime-destroy synthetic terminal is LEGAL from any non-terminal FSM state — including
    edges a bot-driven callback could not take (``joining → completed``). Without ``force`` the same
    edge raises IllegalTransition (the pre-fix 409)."""
    sink = LifecycleSink(store=MeetingStore())
    sink.store.get_or_create("c").status = stale

    # A bot-driven edge that is not in LEGAL_TRANSITIONS must STILL raise (machine not loosened).
    from meeting_api.lifecycle.machine import can_transition

    if not can_transition(stale, to):
        with pytest.raises(IllegalTransition):
            sink.apply_change({"connection_id": "c", "status": to.value})
        # reset the record the failed apply may have left
        sink.store.get_or_create("c").status = stale

    ev = {"connection_id": "c", "status": to.value}
    if to is BotStatus.COMPLETED:
        ev["completion_reason"] = "stopped"
    change = sink.apply_change(
        ev, transition_source=TransitionSource.RUNTIME_DESTROY, force_terminal_on_destroy=True
    )
    assert change.record.status is to
    assert change.transition_source is TransitionSource.RUNTIME_DESTROY
    assert not change.no_op


def test_force_terminal_on_destroy_does_not_reopen_terminal():
    """``force`` never re-opens a terminal record: a destroy arriving after the meeting already
    ``completed`` still raises on a DIFFERENT terminal (``completed → failed``) and no-ops on the same."""
    sink = LifecycleSink(store=MeetingStore())
    rec = sink.store.get_or_create("c")
    rec.status = BotStatus.COMPLETED

    # Different terminal → still illegal, even forced.
    with pytest.raises(IllegalTransition):
        sink.apply_change(
            {"connection_id": "c", "status": "failed"},
            transition_source=TransitionSource.RUNTIME_DESTROY,
            force_terminal_on_destroy=True,
        )
    # Same terminal → idempotent no-op (not a re-advance).
    change = sink.apply_change(
        {"connection_id": "c", "status": "completed"},
        transition_source=TransitionSource.RUNTIME_DESTROY,
        force_terminal_on_destroy=True,
    )
    assert change.no_op is True


def test_force_terminal_only_permits_terminal_targets():
    """``force`` is scoped to TERMINAL targets: a non-terminal target that is illegal for the state
    (``active → joining``) still raises even with the flag set (it is not a licence for any edge)."""
    sink = LifecycleSink(store=MeetingStore())
    sink.store.get_or_create("c").status = BotStatus.ACTIVE
    with pytest.raises(IllegalTransition):
        sink.apply_change(
            {"connection_id": "c", "status": "joining"},
            transition_source=TransitionSource.RUNTIME_DESTROY,
            force_terminal_on_destroy=True,
        )
