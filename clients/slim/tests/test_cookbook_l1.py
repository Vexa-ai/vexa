"""L1 · Client ⇄ gateway contract — the real `Slim` SDK over a fake HTTP transport.

The fixture boundary drops to the wire: a real `Slim` builds real httpx requests, but an `httpx.MockTransport`
answers them with canned api.v1 responses and records what went out. Proves each unary verb hits the right
METHOD + PATH with the right body/params, and parses the response. (The SSE verbs `chat`/`watch` are pinned
at L2 against the real ASGI app, where streaming is faithful.)
"""
from __future__ import annotations

import httpx
import pytest

from vexa_slim import cookbook as cb
from vexa_slim.client import Slim


class Wire:
    """Routes (METHOD, PATH) → a canned api.v1 response, recording every request for assertion."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self._routes = {
            ("POST", "/agent/meeting/process"): {"ok": True, "on": True},
            ("GET", "/agent/workspace/file"): {"content": "FILE"},
            ("GET", "/agent/workspace/tree"): {"files": ["CLAUDE.md", "agents/meeting.md"]},
            ("POST", "/agent/workspace/init"): {"ok": True, "seeded": True},
            ("POST", "/agent/routines"): {"routine": {"name": "digest"}, "job_id": "job-1", "ran_now": True},
            ("GET", "/agent/routines"): {"routines": [{"name": "digest", "enabled": True}]},
            ("PATCH", "/agent/routines/digest/enabled"): {"ok": True, "name": "digest", "enabled": False},
            ("POST", "/bots"): {"ok": True},
            ("DELETE", "/bots/google_meet/abc"): {"ok": True},
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        # a workspace/file read for a missing path → 404 (the client folds 404 → None)
        if request.url.path == "/agent/workspace/file" and "exist" in request.url.params.get("path", ""):
            return httpx.Response(404, json={"detail": "not found"})
        body = self._routes.get((request.method, request.url.path))
        if body is None:
            return httpx.Response(404, json={"detail": "no route"})
        return httpx.Response(200, json=body)

    def last(self) -> httpx.Request:
        return self.seen[-1]


@pytest.fixture
def wire(monkeypatch) -> Wire:
    """Make every `httpx.AsyncClient` the client builds route through our MockTransport."""
    w = Wire()
    transport = httpx.MockTransport(w.handler)
    real = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr("vexa_slim.client.httpx.AsyncClient", patched)
    return w


@pytest.fixture
def slim() -> Slim:
    return Slim("http://gw", "test-key")


async def test_agent_on_meeting_wire(wire, slim):
    await cb.agent_on_meeting(slim, "abc", meet_url="https://meet/x")
    paths = [(r.method, r.url.path) for r in wire.seen]
    assert paths == [("POST", "/bots"), ("POST", "/agent/meeting/process")]
    import json
    assert json.loads(wire.seen[-1].content)["on"] is True       # start_processing → on=True


async def test_schedule_routine_wire(wire, slim):
    import json
    out = await cb.schedule_routine(slim, "digest", cron="0 9 * * *", prompt="brief", run_now=True)
    req = wire.last()
    assert (req.method, req.url.path) == ("POST", "/agent/routines")
    assert json.loads(req.content) == {"name": "digest", "cron": "0 9 * * *", "prompt": "brief", "run_now": True}
    assert out["job_id"] == "job-1"


async def test_list_routines_wire(wire, slim):
    out = await cb.list_routines(slim)
    assert (wire.last().method, wire.last().url.path) == ("GET", "/agent/routines")
    assert out == [{"name": "digest", "enabled": True}]


async def test_set_routine_enabled_wire(wire, slim):
    import json
    out = await cb.set_routine_enabled(slim, "digest", False)
    req = wire.last()
    assert (req.method, req.url.path) == ("PATCH", "/agent/routines/digest/enabled")
    assert json.loads(req.content) == {"enabled": False}
    assert out["enabled"] is False


async def test_init_workspace_wire(wire, slim):
    await cb.init_workspace(slim)
    assert (wire.last().method, wire.last().url.path) == ("POST", "/agent/workspace/init")


async def test_read_workspace_file_wire(wire, slim):
    out = await cb.read_workspace_file(slim, "agents/meeting.md")
    req = wire.last()
    assert (req.method, req.url.path) == ("GET", "/agent/workspace/file")
    assert req.url.params["path"] == "agents/meeting.md"
    assert out == "FILE"


async def test_browse_workspace_wire(wire, slim):
    out = await cb.browse_workspace(slim)
    assert (wire.last().method, wire.last().url.path) == ("GET", "/agent/workspace/tree")
    assert out == ["CLAUDE.md", "agents/meeting.md"]


async def test_read_workspace_file_absent_returns_none(wire, slim):
    out = await cb.read_workspace_file(slim, "does/not/exist.md")  # 404 route → None
    assert out is None
