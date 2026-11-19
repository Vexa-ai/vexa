"""Chat folders over HTTP (#1783): the routes the rail and the agent's tools call.

L2: a real FastAPI app over a sqlite session store and the in-memory session index; no redis, no
runtime, no model. The isolation case is the one the brief names: on a chat shared between two
people, B never sees, renames or files into A's folders."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import session_blobs
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore

try:  # the in-memory transcript store the session tests share, where the tree has one
    from tests._session_fakes import MemBlobs as _MemBlobs

    def _blobs(tmp):
        return _MemBlobs()
except ImportError:  # pragma: no cover — an older tree keeps transcripts in a directory
    def _blobs(tmp):
        return session_blobs.DirBlobs(tmp / "b")
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

SUBJECTS = {"bob@acme.test": "bob"}


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _app(tmp_path, monkeypatch, *, store=True):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    st = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", _blobs(tmp_path)) if store else None
    settings = load_settings(workspaces_dir=str(root), redis_url="", global_system_workspace_path=str(root / "_global"))
    sess = _Sessions()
    app = create_app(Dispatcher(settings, _Runtime(), _Identity()), reader=WorkspaceReader(str(root)),
                     sessions=sess, session_store=st, email_subject_lookup=lambda a: SUBJECTS.get(str(a).lower()))
    return TestClient(app), st, sess


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, st, sess = _app(tmp_path, monkeypatch)
    sess.upsert("alice", "pchat-1", title="Acme renewal", touched=True)
    sess.upsert("alice", "pchat-2", title="Acme onboarding", touched=True)
    sess.upsert("alice", "pchat-3", title="Hiring loop", touched=True)
    yield client, st, sess
    st.close()


def _h(who):
    return {"X-User-Id": who}


def _folders(client, who):
    r = client.get("/api/chat/folders", headers=_h(who))
    assert r.status_code == 200, r.text
    return r.json()


def test_an_agent_files_every_acme_chat_into_a_new_folder(env):
    client, _, _ = env
    got = _folders(client, "alice")
    assert [f["id"] for f in got["folders"]] == ["pinned", "shared", "routines", "ungrouped", "hidden"]
    acme = [c["row"] for c in got["chats"] if "Acme" in c["label"]]
    assert sorted(acme) == ["c:pchat-1", "c:pchat-2"]
    f = client.post("/api/chat/folders", json={"name": "Acme"}, headers=_h("alice")).json()
    assert f["created"] is True
    r = client.post("/api/chat/move", json={"rows": acme, "to": f["id"]}, headers=_h("alice"))
    assert r.status_code == 200, r.text
    by = {c["row"]: c["folder"] for c in _folders(client, "alice")["chats"]}
    assert by == {"c:pchat-1": f["id"], "c:pchat-2": f["id"], "c:pchat-3": "ungrouped"}


def test_rename_collapse_and_dissolve_over_http(env):
    client, _, _ = env
    f = client.post("/api/chat/folders", json={"name": "Acme"}, headers=_h("alice")).json()
    client.post("/api/chat/move", json={"rows": ["c:pchat-1"], "to": f["id"]}, headers=_h("alice"))
    r = client.post("/api/chat/folders/update", json={"folder": f["id"], "name": "Acme Co", "collapsed": True},
                    headers=_h("alice"))
    assert r.status_code == 200 and r.json()["name"] == "Acme Co" and r.json()["collapsed"] is True
    r = client.post("/api/chat/folders/update", json={"folder": "hidden", "name": "x"}, headers=_h("alice"))
    assert r.status_code == 422 and r.json()["detail"]["refused"] == "builtin_folder"
    r = client.post("/api/chat/folders/delete", json={"folder": f["id"]}, headers=_h("alice"))
    assert r.json() == {"ok": True, "moved": ["c:pchat-1"]}
    by = {c["row"]: c["folder"] for c in _folders(client, "alice")["chats"]}
    assert by["c:pchat-1"] == "ungrouped"


def test_hide_is_a_move_to_hidden_and_the_old_marks_route_reads_it(env):
    client, _, _ = env
    client.post("/api/chat/move", json={"rows": ["c:pchat-3"], "to": "hidden"}, headers=_h("alice"))
    assert client.get("/api/chat/marks", headers=_h("alice")).json() == {"pinned": [], "hidden": ["c:pchat-3"]}
    client.post("/api/chat/marks", json={"row": "c:pchat-1", "pinned": True}, headers=_h("alice"))
    assert _folders(client, "alice")["order"]["pinned"] == ["c:pchat-1"]
    client.post("/api/chat/move", json={"rows": ["c:pchat-3"], "to": "ungrouped"}, headers=_h("alice"))
    assert client.get("/api/chat/marks", headers=_h("alice")).json()["hidden"] == []


def test_old_marks_are_filed_once_when_the_folders_are_first_read(env):
    client, _, sess = env
    sess.mark_row("alice", "c:pchat-1", pinned=True)
    sess.mark_row("alice", "c:pchat-3", hidden=True)
    got = _folders(client, "alice")
    assert got["filings"] == {"c:pchat-1": "pinned", "c:pchat-3": "hidden"}


def test_filing_someone_elses_chat_id_touches_only_your_own_rail(env):
    """A filing is the filer's own preference. Naming another person's chat id files an empty chat
    of that name on YOUR rail; their chat, their rail and their conversation are untouched."""
    client, _, sess = env
    r = client.post("/api/chat/move", json={"rows": ["c:pchat-1"], "to": "pinned"}, headers=_h("mallory"))
    assert r.status_code == 200
    assert _folders(client, "alice")["filings"] == {}
    assert [x["session"] for x in sess.list("alice") if x["session"] == "pchat-1"] == ["pchat-1"]
    assert client.get("/api/sessions/pchat-1/history", headers=_h("mallory")).json().get("turns", []) == []
    r = client.post("/api/chat/move", json={"rows": ["c:pchat-1"], "to": "f-000000000000"}, headers=_h("alice"))
    assert r.status_code == 404 and r.json()["detail"]["refused"] == "no_such_folder"
    r = client.post("/api/chat/move", json={"rows": ["c:pchat-1"], "to": "f-000000000000"}, headers=_h("alice"))
    assert r.status_code == 404 and r.json()["detail"]["refused"] == "no_such_folder"


def test_on_a_shared_chat_b_never_sees_or_touches_as_folders(env):
    client, st, _ = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test"},
                      headers=_h("alice")).json()["session_id"]
    fa = client.post("/api/chat/folders", json={"name": "Acme"}, headers=_h("alice")).json()
    assert client.post("/api/chat/move", json={"rows": ["c:pchat-1"], "to": fa["id"]}, headers=_h("alice")).status_code == 200
    # bob files the same conversation in his own place
    r = client.post("/api/chat/move", json={"rows": [f"c:{sid}"], "to": "pinned"}, headers=_h("bob"))
    assert r.status_code == 200, r.text
    bob = _folders(client, "bob")
    assert fa["id"] not in [f["id"] for f in bob["folders"]]
    assert "Acme" not in [f["name"] for f in bob["folders"]]
    assert bob["filings"] == {f"c:{sid}": "pinned"}
    for path, body in (("/api/chat/folders/update", {"folder": fa["id"], "name": "Mine"}),
                       ("/api/chat/folders/delete", {"folder": fa["id"]}),
                       ("/api/chat/move", {"rows": [f"c:{sid}"], "to": fa["id"]})):
        r = client.post(path, json=body, headers=_h("bob"))
        assert r.status_code == 404 and r.json()["detail"]["refused"] == "no_such_folder", (path, r.text)
    alice = _folders(client, "alice")
    assert next(f for f in alice["folders"] if f["id"] == fa["id"])["name"] == "Acme"
    assert alice["filings"] == {"c:pchat-1": fa["id"]}


def test_deleting_a_chat_drops_its_filing(env):
    client, _, _ = env
    f = client.post("/api/chat/folders", json={"name": "Acme"}, headers=_h("alice")).json()
    client.post("/api/chat/move", json={"rows": ["c:pchat-2"], "to": f["id"]}, headers=_h("alice"))
    assert client.delete("/api/sessions/pchat-2", headers=_h("alice")).status_code == 200
    assert "c:pchat-2" not in _folders(client, "alice")["filings"]


def test_without_the_session_store_folders_say_so_and_marks_keep_working(tmp_path, monkeypatch):
    client, _, sess = _app(tmp_path, monkeypatch, store=False)
    sess.upsert("alice", "pchat-1", title="x", touched=True)
    r = client.get("/api/chat/folders", headers=_h("alice"))
    assert r.status_code == 503 and r.json()["detail"]["refused"] == "folders_unavailable"
    r = client.post("/api/chat/marks", json={"row": "c:pchat-1", "pinned": True}, headers=_h("alice"))
    assert r.json() == {"pinned": ["c:pchat-1"], "hidden": []}


def test_every_chat_on_the_rail_can_be_filed_including_one_the_server_has_not_indexed_yet(env):
    """Founder, moving a chat to Hidden: "Not a chat on this person's rail". The rail also shows a
    chat this browser started that has had no turn yet (no row in the server's index), so its
    filing was refused. Filing such a chat indexes it for this person on the spot."""
    client, _, sess = env
    r = client.post("/api/chat/move", json={"rows": ["c:pchat-local-1"], "to": "hidden"}, headers=_h("alice"))
    assert r.status_code == 200, r.text
    assert r.json()["filings"]["c:pchat-local-1"] == "hidden"
    assert any(x["session"] == "pchat-local-1" for x in sess.list("alice"))
    # a routine's chat and an ordinary one are filed the same way
    sess.upsert("alice", "routine-brief-1", title="Daily brief", routine={"flow": "brief", "scope": "user"})
    assert client.post("/api/chat/move", json={"rows": ["c:routine-brief-1", "c:pchat-1"], "to": "pinned"},
                       headers=_h("alice")).status_code == 200


def test_filing_never_indexes_a_meeting_chat_for_a_meeting_the_person_cannot_read(env):
    client, _, sess = env
    r = client.post("/api/chat/move", json={"rows": ["c:meet-999"], "to": "hidden"}, headers=_h("alice"))
    assert r.status_code == 404
    assert not any(x["session"] == "meet-999" for x in sess.list("alice"))
