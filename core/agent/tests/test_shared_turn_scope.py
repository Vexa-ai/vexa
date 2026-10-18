"""A turn other people read reaches only the shared scope it was dispatched with.

A worker answering into a shared conversation (`delegation.shared`) carries a ceiling of the
workspaces every participant reads. Its own desk, an omitted workspace (which means the desk) and
its private `_system` store are not in that scope, so each is refused there. The same worker in a
private turn is unchanged. A meeting reference in a shared turn answers `unavailable` unless every
person in the conversation may read the meeting.
"""
from __future__ import annotations

import pytest

from control_plane import identity_token
from tests.test_delegation_ceiling_verbs import KEY, PERSON, _build

SCOPE = "team"


def _worker(*, shared: bool) -> dict:
    dlg = {"regime": "human", "workspaces": [SCOPE]}
    if shared:
        dlg["shared"] = True
    claims = {"sub": PERSON, "email": "ada@example.com", "delegation": dlg}
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


def _out_of_scope(r) -> bool:
    if r.status_code != 403:
        return False
    detail = r.json().get("detail")
    return isinstance(detail, dict) and detail.get("refused") == "out_of_scope"


@pytest.fixture
def client(monkeypatch, tmp_path):
    return _build(monkeypatch, tmp_path)


READS = [
    ("tree of _system", "GET", "/api/workspace/tree", {"slug": "_system"}, None),
    ("read _system", "GET", "/api/workspace/file", {"slug": "_system", "path": "README.md"}, None),
    ("tree, no slug", "GET", "/api/workspace/tree", {}, None),
    ("read, no slug", "GET", "/api/workspace/file", {"path": "README.md"}, None),
    ("read own desk by id", "GET", "/api/workspace/file", {"slug": PERSON, "path": "README.md"}, None),
    ("write, no slug", "PUT", "/api/workspace/file", None, {"path": "notes/x.md", "content": "x"}),
    ("write personal", "PUT", "/api/workspace/file", None,
     {"path": "notes/x.md", "content": "x", "slug": "personal"}),
    ("write own desk by id", "PUT", "/api/workspace/file", None,
     {"path": "notes/x.md", "content": "x", "slug": PERSON}),
]


@pytest.mark.parametrize("name,method,path,params,body", READS, ids=[r[0] for r in READS])
def test_a_shared_turn_reaches_nothing_outside_its_scope(client, name, method, path, params, body):
    r = client.request(method, path, params=params, json=body, headers=_worker(shared=True))
    assert _out_of_scope(r), (name, r.status_code, r.text)


@pytest.mark.parametrize("name,method,path,params,body", READS, ids=[r[0] for r in READS])
def test_a_private_turn_keeps_its_own_desk_and_system_store(client, name, method, path, params, body):
    r = client.request(method, path, params=params, json=body, headers=_worker(shared=False))
    assert not _out_of_scope(r), (name, r.status_code, r.text)


def test_a_shared_turn_may_still_name_its_shared_scope(client):
    r = client.get("/api/workspace/tree", params={"slug": SCOPE}, headers=_worker(shared=True))
    assert not _out_of_scope(r), r.text


# ── a meeting reference in a shared turn ──────────────────────────────────────────────────────────

def test_a_meeting_ref_in_a_shared_turn_is_unavailable_unless_everyone_reads_it(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from control_plane.api import _Sessions, create_app
    from control_plane.dispatch import Dispatcher
    from control_plane.session_store import SessionStore
    from control_plane.workspace_reader import WorkspaceReader
    from shared.config import load_settings
    from tests._session_fakes import MemBlobs

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    root.mkdir()
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret="s",
                             global_system_workspace_path=str(root / "_global"))

    class _Runtime:
        def spawn(self, workload_id, profile, env):
            return workload_id

        def await_done(self, workload_id, timeout_sec=0.0):
            return "completed"

    class _Identity:
        def mint(self, subject, launcher, workspaces, tools):
            return "tok"

    def _meeting(user_id, meeting_id, workspaces=None):
        # 147: the speaker's own private meeting, only its owner reads it; 148: both people read it
        if str(meeting_id) == "148" and user_id in ("u_alice", "u_bob"):
            return {"id": 148, "title": "Team call"}
        return {"id": int(meeting_id), "title": "Private call"} if user_id == "u_alice" else None

    c = TestClient(create_app(Dispatcher(settings, _Runtime(), _Identity()), reader=WorkspaceReader(str(root)),
                              sessions=_Sessions(), session_store=store, meeting_owner_lookup=_meeting))
    chat = store.create("u_alice", name="board", title="board")
    store.add_participant(chat["id"], "u_alice", "u_bob")
    base = {"X-User-Id": "u_alice", "X-User-Regime": "human", "X-User-Delegation-Workspaces": SCOPE,
            "X-User-Delegation-Unit": f"agent-u_alice-chat-{chat['id']}"}
    private = c.get("/api/refs/resolve", params={"ref": "/r/meeting/147"}, headers=base).json()
    assert private["access"] == "readable"
    shared = c.get("/api/refs/resolve", params={"ref": "/r/meeting/147"},
                   headers={**base, "X-User-Delegation-Shared": "1"}).json()
    assert shared["access"] == "unavailable" and "Private" not in str(shared)
    both = c.get("/api/refs/resolve", params={"ref": "/r/meeting/148"},
                 headers={**base, "X-User-Delegation-Shared": "1"}).json()
    assert both["access"] == "readable", both
    store.close()
