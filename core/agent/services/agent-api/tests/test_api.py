"""Front-door L2 tests — the agent-api HTTP surface over fakes (no runtime, no claude needed).

Proves: /health is live; /invocations validates + dispatches (and 400s a bad envelope); /api/chat
streams injected UnitEvents as SSE and records the session; chat is an honest 501 with no runner wired.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from agent_api.api import create_app
from agent_api.config import load_settings
from agent_api.dispatch import Dispatcher

VALID_INV = {
    "trigger": "message",
    "subject": "u_jane",
    "workspace_repo": "https://git.example.com/acme/vexa-ws-jane.git",
    "context": {"kind": "none"},
    "plan": {"prompt": "hi"},
    "lifecycle": "warm",
}


class _FakeRuntime:
    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, env))
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


def _client(chat_runner=None) -> TestClient:
    return TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime()), chat_runner=chat_runner))


def test_health_ok():
    r = _client().get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_invocations_dispatches():
    r = _client().post("/invocations", json=VALID_INV)
    assert r.status_code == 202 and r.json()["workload_id"]


def test_invocations_rejects_nonconformant():
    r = _client().post("/invocations", json={"trigger": "message"})  # missing required fields
    assert r.status_code == 400


def test_chat_501_without_runner():
    r = _client().post("/api/chat", json={"prompt": "hi", "subject": "u"})
    assert r.status_code == 501


def test_chat_streams_sse_and_records_session():
    class _FakeChat:
        def run(self, prompt, *, subject, session=None):
            yield {"type": "message-delta", "text": "hi"}
            yield {"type": "commit", "sha": "abc123"}

    c = _client(_FakeChat())
    r = c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1"})
    assert r.status_code == 200
    body = r.text
    assert "data: " in body and '"message-delta"' in body and '"commit"' in body
    sessions = c.get("/api/sessions", params={"subject": "u_jane"}).json()["sessions"]
    assert "s1" in sessions


def test_workspace_read_and_traversal_guard(tmp_path):
    from agent_api.workspace_reader import WorkspaceReader
    p = tmp_path / "u_jane" / "kg" / "entities" / "person"
    p.mkdir(parents=True)
    (p / "jane.md").write_text("---\ntype: person\nid: jane\ntitle: Jane\n---\nbody\n")
    c = TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime()), reader=WorkspaceReader(str(tmp_path))))
    files = c.get("/api/workspace/tree", params={"subject": "u_jane"}).json()["files"]
    assert "kg/entities/person/jane.md" in files
    got = c.get("/api/workspace/file", params={"subject": "u_jane", "path": "kg/entities/person/jane.md"})
    assert got.status_code == 200 and "title: Jane" in got.json()["content"]
    assert c.get("/api/workspace/file", params={"subject": "u_jane", "path": "../../etc/passwd"}).status_code == 400
    assert c.get("/api/workspace/file", params={"subject": "u_jane", "path": "nope.md"}).status_code == 404
