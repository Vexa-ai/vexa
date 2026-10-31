"""#1974: a bot live across a meeting-api swap keeps its own producer-stamped terminal.

meeting-api never acknowledges a terminal it did not persist (503, in-memory record evicted, no
side effect fired), the status_change envelope is recorded only after the persist, and an admitted
session closed without the bot's own report logs ``lifecycle_terminal_without_producer_time``.
"""
from __future__ import annotations

import asyncio
import copy
import json

import httpx

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.machine import TransitionSource
from meeting_api.webhooks import DeliveryResult

LIFECYCLE = "/bots/internal/callback/lifecycle"
RUNTIME = "/runtime/callback"
SID = "sess-uid"
T_JOIN = "2026-10-31T13:00:00.000Z"
T_WAIT = "2026-10-31T13:00:10.000Z"
T_ACTIVE = "2026-10-31T13:00:30.000Z"
T_DONE = "2026-10-31T13:25:42.000Z"
KERNEL_AT = "2026-10-31T13:26:07.482913+00:00"


class _FlakyRepo(InMemoryMeetingRepo):
    """Raises from update_meeting_status (before any mutation) while a flag is set."""

    def __init__(self):
        super().__init__()
        self.fail_terminal = False
        self.fail_nonterminal = False

    async def update_meeting_status(self, *, session_uid, status, **kw):
        terminal = status in ("completed", "failed")
        if (terminal and self.fail_terminal) or (not terminal and self.fail_nonterminal):
            raise RuntimeError("pool exhausted")
        return await super().update_meeting_status(session_uid=session_uid, status=status, **kw)


class _Sink:
    def __init__(self):
        self.calls = []

    async def deliver(self, url, envelope, webhook_secret=None, *, scope="per-client",
                      events_config=None, label="", metadata=None):
        self.calls.append(envelope)
        return DeliveryResult(status="delivered", status_code=200)


class _Redis:
    def __init__(self):
        self.published = []

    async def publish(self, channel, data):
        self.published.append((channel, data))
        return 1

    async def xadd(self, stream, payload):
        return "1-0"


async def _seed(repo):
    m = await repo.create_meeting(
        user_id=1, platform="google_meet", native_meeting_id="m1",
        data={
            "transcription_provider": "none",
            "webhook_url": "https://hook.example/x",
            "webhook_events": {"meeting.status_change": True, "meeting.started": True,
                               "meeting.completed": True, "bot.failed": True},
        },
    )
    await repo.create_session(meeting_id=m["id"], session_uid=SID)
    await repo.set_bot_container(meeting_id=m["id"], bot_container_id="wl-1")
    repo.set_status(m["id"], "requested")
    return m


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tsrv")


def _ev(status, ts, **extra):
    return {"connection_id": SID, "status": status, "timestamp": ts, **extra}


async def _admit(c, *, wait=False):
    steps = [("joining", T_JOIN)] + ([("awaiting_admission", T_WAIT)] if wait else [])
    for status, ts in steps + [("active", T_ACTIVE)]:
        r = await c.post(LIFECYCLE, json=_ev(status, ts))
        assert r.status_code == 200, r.text


DONE = _ev("completed", T_DONE, completion_reason="stopped")


def _completed_envelopes(app):
    return [e for e in app.state.status_change_webhooks
            if e["data"]["status_change"]["new_status"] == "completed"]


def _typed(sink, event_type):
    return [e for e in sink.calls if e.get("event_type") == event_type]


def _status_frames(redis, status):
    return [d for ch, d in redis.published if json.loads(d).get("payload", {}).get("status") == status
            or json.loads(d).get("status") == status]


def _events(capsys, name):
    out = []
    for line in capsys.readouterr().out.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("event") == name:
            out.append(obj)
    return out


def _bot_terminal_503_then_200():
    """A3's sequence; returns its observations and the live app for A5."""
    async def run():
        repo, sink, redis = _FlakyRepo(), _Sink(), _Redis()
        m = await _seed(repo)
        app = create_app(meeting_repo=repo, webhook_sink=sink, redis=redis)
        async with _client(app) as c:
            await _admit(c)
            repo.fail_terminal = True
            first = await c.post(LIFECYCLE, json=DONE)
            after_first = {
                "row_status": repo._meetings[m["id"]]["status"],
                "envelopes": len(_completed_envelopes(app)),
                "completed_events": len(_typed(sink, "meeting.completed")),
                "frames": len(_status_frames(redis, "completed")),
            }
            repo.fail_terminal = False
            second = await c.post(LIFECYCLE, json=DONE)
            snapshot = copy.deepcopy(repo._meetings[m["id"]])
            envelopes_before_runtime = len(app.state.status_change_webhooks)
            rc = await c.post(RUNTIME, json={"workloadId": "wl-1", "state": "destroyed",
                                             "at": KERNEL_AT})
        return locals()

    return asyncio.run(run())


def test_a3_unsaved_terminal_is_503_with_no_side_effect_and_retry_lands_once():
    r = _bot_terminal_503_then_200()
    assert r["first"].status_code == 503, r["first"].text
    assert r["first"].json()["detail"] == "lifecycle terminal not persisted; retry"
    assert r["after_first"] == {"row_status": "active", "envelopes": 0,
                                "completed_events": 0, "frames": 0}
    assert r["second"].status_code == 200, r["second"].text
    row = r["repo"]._meetings[r["m"]["id"]]
    assert row["status"] == "completed"
    closing = row["data"]["status_transition"][-1]
    assert closing["to"] == "completed"
    assert closing["source"] == "bot_callback"
    assert closing["timestamp_source"] == "producer"
    assert closing["timestamp"] == T_DONE
    assert len(_completed_envelopes(r["app"])) == 1
    completed = _typed(r["sink"], "meeting.completed")
    assert len(completed) == 1
    assert completed[0]["data"]["meeting"]["service_provenance"]["bot_outcome"] == "served"
    assert len(_status_frames(r["redis"], "completed")) == 2  # bm: + u: channels, one advance


def test_a5_runtime_destroy_after_bot_terminal_changes_nothing():
    r = _bot_terminal_503_then_200()
    assert r["rc"].status_code == 200
    row = r["repo"]._meetings[r["m"]["id"]]
    assert row == r["snapshot"]
    assert row["data"]["status_transition"][-1]["source"] == "bot_callback"
    assert row["data"]["service_provenance"]["bot_departed_at"] == T_DONE
    assert len(r["app"].state.status_change_webhooks) == r["envelopes_before_runtime"]
    assert len(_completed_envelopes(r["app"])) == 1


def test_a4_completed_on_fresh_instance_after_swap_is_served():
    async def run():
        repo = _FlakyRepo()
        m = await _seed(repo)
        async with _client(create_app(meeting_repo=repo)) as c1:
            await _admit(c1, wait=True)
        async with _client(create_app(meeting_repo=repo)) as c2:
            resp = await c2.post(LIFECYCLE, json=DONE)
        return resp, repo._meetings[m["id"]]

    resp, row = asyncio.run(run())
    assert resp.status_code == 200, resp.text
    block = row["data"]["service_provenance"]
    assert block["bot_outcome"] == "served"
    assert block["bot_departed_at"] == T_DONE


def test_a6_nonterminal_persist_failure_stays_200_with_envelope():
    async def run():
        repo = _FlakyRepo()
        await _seed(repo)
        app = create_app(meeting_repo=repo)
        async with _client(app) as c:
            assert (await c.post(LIFECYCLE, json=_ev("joining", T_JOIN))).status_code == 200
            repo.fail_nonterminal = True
            resp = await c.post(LIFECYCLE, json=_ev("active", T_ACTIVE))
        return resp, app

    resp, app = asyncio.run(run())
    assert resp.status_code == 200, resp.text
    assert app.state.status_change_webhooks[-1]["data"]["status_change"]["new_status"] == "active"


def test_a6_normal_producer_terminal_block_unchanged(capsys):
    async def run():
        repo = _FlakyRepo()
        m = await _seed(repo)
        async with _client(create_app(meeting_repo=repo)) as c:
            await _admit(c)
            assert (await c.post(LIFECYCLE, json=DONE)).status_code == 200
        return repo._meetings[m["id"]]

    row = asyncio.run(run())
    assert row["data"]["service_provenance"] == {
        "bot_admitted_at": T_ACTIVE,
        "bot_departed_at": T_DONE,
        "bot_outcome": "served",
        "transcription_provider": "none",
        "transcription_outcome": "disabled",
        "lifecycle_contract_version": "2026-07-28",
    }
    # A7: the bot's own producer terminal writes no operator line.
    assert _events(capsys, "lifecycle_terminal_without_producer_time") == []


def _server_terminal(kind):
    async def run():
        repo = _FlakyRepo()
        m = await _seed(repo)
        app = create_app(meeting_repo=repo)
        async with _client(app) as c:
            if kind == "never_admitted":
                assert (await c.post(LIFECYCLE, json=_ev("joining", T_JOIN))).status_code == 200
            else:
                await _admit(c)
            if kind == "sweep":
                code, _ = await app.state.apply_lifecycle_event(
                    {"connection_id": SID, "status": "completed",
                     "completion_reason": "left_alone"},
                    transition_source=TransitionSource.RUNTIME_DESTROY,
                    force_terminal_on_destroy=True,
                )
                assert code == 200
            else:
                rc = await c.post(RUNTIME, json={"workloadId": "wl-1", "state": "destroyed",
                                                 "at": KERNEL_AT})
                assert rc.status_code == 200
        return m, repo._meetings[m["id"]]

    return asyncio.run(run())


def test_a7_runtime_callback_terminal_logs_one_event(capsys):
    m, row = _server_terminal("runtime")
    assert row["status"] == "completed"
    events = _events(capsys, "lifecycle_terminal_without_producer_time")
    assert len(events) == 1
    assert events[0]["level"] == "warning" and events[0]["span"] == "lifecycle.terminal"
    assert events[0]["fields"] == {
        "meeting_id": m["id"], "transition_source": "runtime_destroy",
        "timestamp_source": "producer", "terminal_status": "completed",
        "completion_reason": "left_alone",
    }


def test_a7_sweep_terminal_logs_one_event(capsys):
    m, row = _server_terminal("sweep")
    assert row["status"] == "completed"
    assert row["data"].get("service_provenance") is None
    events = _events(capsys, "lifecycle_terminal_without_producer_time")
    assert len(events) == 1
    assert events[0]["fields"] == {
        "meeting_id": m["id"], "transition_source": "runtime_destroy",
        "timestamp_source": "receiver", "terminal_status": "completed",
        "completion_reason": "left_alone",
    }


def test_a7_never_admitted_terminal_logs_nothing(capsys):
    _m, row = _server_terminal("never_admitted")
    assert row["status"] == "failed"
    assert _events(capsys, "lifecycle_terminal_without_producer_time") == []
