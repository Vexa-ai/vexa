"""A CHAT NOBODY WROTE IN IS NOT A ROW (#1783, founder on the live desktop 2026-10-15: "empty new
chats after every new attempt to do something in the desktop").

The rail is this index. Writing a draft chat's scope — its target, its mounts, its reads — creates a
record for it before anybody has said anything, and every such record used to come back from
`GET /api/sessions` as a "New chat" row. The rule: a chat is listed once it has a message (or a file
attached in it, or a meeting, routine or ask it was made for). A scope write alone lists nothing.

L2: a real FastAPI app over fakes, no redis, no runtime, no model.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

WHO = {"X-User-Id": "u_priya"}


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Reader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "message-delta", "text": "hi"}
        yield {"type": "turn-complete"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    sess = _Sessions()
    app = create_app(Dispatcher(settings, _Runtime(), _Identity()), stream_reader=_Reader(),
                     reader=WorkspaceReader(str(root)), sessions=sess)
    c = TestClient(app)
    c.sess = sess
    return c


def _listed(client) -> set[str]:
    return {r["session"] for r in client.get("/api/sessions", headers=WHO).json()["sessions"]}


def test_a_scope_write_on_a_draft_lists_no_chat(client):
    for i in range(3):
        client.post("/api/chat/target", json={"session": f"pchat-draft{i}", "workspace": ""}, headers=WHO)
        client.sess.set_target("u_priya", f"pchat-scope{i}", "")
        client.sess.add_workspace("u_priya", f"pchat-scope{i}", "personal")
    assert _listed(client) == set()


def test_a_chat_with_a_message_is_listed(client):
    client.sess.set_target("u_priya", "pchat-real", "")
    assert client.post("/api/chat", json={"prompt": "hello there", "session": "pchat-real"}, headers=WHO).status_code == 200
    assert _listed(client) == {"pchat-real"}


def test_a_chat_made_for_a_meeting_or_a_routine_is_listed_before_its_first_message(client):
    client.sess.upsert("u_priya", "pchat-meet", meeting="42")
    client.sess.upsert("u_priya", "pchat-routine", routine={"flow": "morning", "scope": "user:u_priya", "version": 1})
    assert {"pchat-meet", "pchat-routine"} <= _listed(client)
