"""Front-door L2 tests — the agent-api HTTP surface over fakes (no runtime, no claude needed).

Proves: /health is live; /invocations validates + dispatches (and 400s a bad envelope); /api/chat
spawns a now-dispatch and streams its Stream back as SSE; chat is an honest 501 with no relay wired.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from agent_api.api import create_app
from agent_api.config import load_settings
from agent_api.dispatch import Dispatcher

VALID_INV = {
    "identity": {"subject": "u_jane", "launcher": "user:u_jane"},
    "runner": "claude-code",
    "workspaces": [{"id": "u_jane", "mode": "rw"}],
    "trigger": "message",
    "context": {"kind": "none"},
    "start": {"entrypoint": {"inline": "hi"}},
}


class _FakeRuntime:
    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, env))
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _FakeReader:
    """A StreamReader fake — yields the dispatch's UnitEvents (what redis XREAD would relay)."""
    def read(self, unit_id):
        yield {"type": "message-delta", "text": "hi"}
        yield {"type": "commit", "sha": "abc123"}


def _client(stream_reader=None) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), stream_reader=stream_reader,
    ))


def test_health_ok():
    r = _client().get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_invocations_dispatches():
    r = _client().post("/invocations", json=VALID_INV)
    assert r.status_code == 202 and r.json()["workload_id"]


def test_invocations_rejects_nonconformant():
    r = _client().post("/invocations", json={"trigger": "message"})  # missing identity/workspaces/start
    assert r.status_code == 400


def test_chat_501_without_reader():
    r = _client().post("/api/chat", json={"prompt": "hi", "subject": "u"})
    assert r.status_code == 501


def test_chat_streams_sse_and_records_session():
    c = _client(_FakeReader())
    r = c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1"})
    assert r.status_code == 200
    body = r.text
    assert "data: " in body and '"message-delta"' in body and '"commit"' in body
    sessions = c.get("/api/sessions", params={"subject": "u_jane"}).json()["sessions"]
    assert any(s["session"] == "s1" for s in sessions)
    assert r.headers["X-Unit-Id"] == "agent-u_jane-chat-s1"  # the per-thread warm unit id


def test_chat_reset_drops_session_and_continuity_file(tmp_path):
    from agent_api.workspace_reader import WorkspaceReader

    # plant a thread's continuity file in the subject's workspace
    sess_dir = tmp_path / "u_jane" / ".claude" / "sessions"
    sess_dir.mkdir(parents=True)
    (sess_dir / "s1.session").write_text("SID")
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        stream_reader=_FakeReader(), reader=WorkspaceReader(str(tmp_path)),
    ))
    c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1"})
    assert any(s["session"] == "s1" for s in c.get("/api/sessions", params={"subject": "u_jane"}).json()["sessions"])

    r = c.post("/api/chat/reset", json={"prompt": "", "subject": "u_jane", "session": "s1"})
    assert r.status_code == 200
    assert not (sess_dir / "s1.session").exists()  # continuity file deleted
    assert not any(s["session"] == "s1" for s in c.get("/api/sessions", params={"subject": "u_jane"}).json()["sessions"])


def test_chat_defaults_session_to_main(tmp_path):
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), stream_reader=_FakeReader(),
    ))
    r = c.post("/api/chat", json={"prompt": "no session given", "subject": "u_jane"})
    assert r.headers["X-Chat-Session"] == "main"
    assert r.headers["X-Unit-Id"] == "agent-u_jane-chat-main"
    rows = c.get("/api/sessions", params={"subject": "u_jane"}).json()["sessions"]
    assert rows[0]["session"] == "main" and rows[0]["title"] == "no session given"


def test_workspace_read_and_traversal_guard(tmp_path):
    from agent_api.workspace_reader import WorkspaceReader
    p = tmp_path / "u_jane" / "kg" / "entities" / "person"
    p.mkdir(parents=True)
    (p / "jane.md").write_text("---\ntype: person\nid: jane\ntitle: Jane\n---\nbody\n")
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), reader=WorkspaceReader(str(tmp_path)),
    ))
    files = c.get("/api/workspace/tree", params={"subject": "u_jane"}).json()["files"]
    assert "kg/entities/person/jane.md" in files
    got = c.get("/api/workspace/file", params={"subject": "u_jane", "path": "kg/entities/person/jane.md"})
    assert got.status_code == 200 and "title: Jane" in got.json()["content"]
    assert c.get("/api/workspace/file", params={"subject": "u_jane", "path": "../../etc/passwd"}).status_code == 400
    assert c.get("/api/workspace/file", params={"subject": "u_jane", "path": "nope.md"}).status_code == 404
