"""Delete a chat for good — `DELETE /api/sessions/{session_id}` (api.v1), served by agent-api.

The rail's Delete used to write a tombstone into one browser's storage and nothing else: the chat,
its conversation and every other device's row were untouched. These pin the server-side delete: the
caller's own chat only, its conversation gone, workspace pages kept, a meeting's row hidden for the
caller rather than coming straight back."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.api_shared import _Sessions
from control_plane.dispatch import Dispatcher
from control_plane.identity_token import DELEGATION_HEADERS
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _thread(ws: Path, session: str, sid: str, text: str) -> Path:
    (ws / ".claude" / "sessions").mkdir(parents=True, exist_ok=True)
    (ws / ".claude" / "sessions" / f"{session}.session").write_text(sid + "\n")
    proj = ws / ".claude" / "projects" / "-workspace"
    proj.mkdir(parents=True, exist_ok=True)
    t = proj / f"{sid}.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
    return t


JANE = {"X-User-Id": "u_jane"}
MALLORY = {"X-User-Id": "u_mallory"}


@pytest.fixture()
def rig(tmp_path):
    root = tmp_path / "store"
    sess = _Sessions()
    desk = root / ".system" / "u_jane"
    transcript = _thread(desk, "pchat-1", "sid-1", "review my inbox")
    _thread(desk, "meet-42", "sid-42", "what did they decide")
    page = root / "u_jane" / "notes" / "inbox.md"
    page.parent.mkdir(parents=True)
    page.write_text("a page the chat wrote")
    sess.upsert("u_jane", "pchat-1", title="Review email inbox", touched=True)
    sess.upsert("u_jane", "meet-42", title="Weekly sync", touched=True)
    sess.upsert("u_jane", "pchat-2", title="Bound chat", touched=True, meeting="77")
    sess.mark_row("u_jane", "c:pchat-1", pinned=True)
    sess.rail_order("u_jane", ["c:pchat-1", "c:meet-42"])
    client = TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root)), _FakeRuntime(), _FakeIdentity()),
        sessions=sess, reader=WorkspaceReader(str(root))))
    return client, sess, transcript, page


def test_delete_removes_the_chat_its_conversation_and_its_marks(rig):
    client, sess, transcript, page = rig
    assert client.get("/api/sessions/pchat-1/history", headers=JANE).json()["turns"]
    r = client.delete("/api/sessions/pchat-1", headers=JANE)
    assert r.status_code == 200, r.text
    assert r.json()["hidden_meeting"] is None and r.json()["files_removed"] >= 2
    assert "pchat-1" not in [s["session"] for s in client.get("/api/sessions", headers=JANE).json()["sessions"]]
    assert not transcript.exists()
    assert client.get("/api/sessions/pchat-1/history", headers=JANE).status_code == 404
    assert client.get("/api/chat/marks", headers=JANE).json() == {"pinned": [], "hidden": []}
    assert client.get("/api/chat/order", headers=JANE).json()["order"] == ["c:meet-42"]
    # what the chat wrote into a workspace is the workspace's
    assert page.read_text() == "a page the chat wrote"
    assert client.delete("/api/sessions/pchat-1", headers=JANE).status_code == 404


def test_deleting_a_meeting_chat_hides_the_meeting_row_and_keeps_the_meeting(rig):
    """The founder's 'delete chat is not working': the meeting's own chat went, and the meeting came
    straight back as a derived row with the same name. The meeting is not the chat's to delete; the
    row is hidden for the person who deleted the chat."""
    client, sess, *_ = rig
    r = client.delete("/api/sessions/meet-42", headers=JANE)
    assert r.status_code == 200 and r.json()["hidden_meeting"] == "m:42"
    r = client.delete("/api/sessions/pchat-2", headers=JANE)
    assert r.json()["hidden_meeting"] == "m:77"
    assert client.get("/api/chat/marks", headers=JANE).json()["hidden"] == ["m:42", "m:77"]


def test_another_persons_chat_cannot_be_deleted(rig):
    client, sess, transcript, _ = rig
    assert client.delete("/api/sessions/pchat-1", headers=MALLORY).status_code == 404
    assert transcript.exists()
    assert "pchat-1" in [s["session"] for s in sess.list("u_jane")]


def test_an_agent_acting_for_the_person_cannot_delete(rig):
    client, sess, transcript, _ = rig
    header = next(iter(DELEGATION_HEADERS.values()))
    r = client.delete("/api/sessions/pchat-1", headers={**JANE, header: "human"})
    assert r.status_code == 403
    assert transcript.exists()


def test_a_session_id_outside_the_rule_is_refused(rig):
    client, *_ = rig
    for bad in ("..", "a%2Fb", "-x"):
        assert client.delete(f"/api/sessions/{bad}", headers=JANE).status_code in (404, 422)


def test_a_chat_with_queued_messages_is_not_deleted(rig, monkeypatch):
    client, sess, transcript, _ = rig
    from control_plane.routers import chats as chats_mod
    monkeypatch.setattr(chats_mod, "inbox_pending", lambda *a, **k: [{"id": "q1"}])
    # the router binds the name at import, so rebuild the app with the patched reader
    app = create_app(Dispatcher(load_settings(workspaces_dir=str(transcript.parents[5])), _FakeRuntime(), _FakeIdentity()),
                     sessions=sess, reader=WorkspaceReader(str(transcript.parents[5])))
    r = TestClient(app).delete("/api/sessions/pchat-1", headers=JANE)
    assert r.status_code == 409
    assert transcript.exists()
