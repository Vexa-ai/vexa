"""Notifications over HTTP, emitted by the routes that do the acts (#1783).

Each kind has one writer — adding a person to a chat (``chat_added`` / ``chat_shared``), an
@-mention (``chat_mentioned``), a routine's run (``routine_posted``), inviting an account, adding one
to a workspace, accepting or declining an invite. Each is delivered to the recipient's own channel
and stored for them alone; the text names the actor and the object, never what was said. A chat
shared with someone lands in their rail's built-in Shared with me section, and leaving takes it off.

L2: FastAPI over a sqlite session store, real git workspaces on disk, a list for a redis bus."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests._session_fakes import MemBlobs

ALICE, JOE, DAVE = "u_alice", "u_joe", "u_dave"
MAIL = {ALICE: "alice@acme.test", JOE: "joe@acme.test", DAVE: "dave@other.test"}
SECRET = "internal-s"
SECRET_LINE = "the board approved the merger at 41 dollars a share"


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
        yield {"type": "message-delta", "text": "ok"}
        yield {"type": "turn-complete"}


def _git(work: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True, check=True)


def _ws(root: Path, slug: str, owner: str, idx, *members) -> None:
    ws = root / slug
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    m.ensure_owner(root, slug, owner, index=idx, email=MAIL[owner], commit_fn=m.policy_commit)
    for who, role in members:
        m.grant_membership(root, slug, who, role, added_by=owner, index=idx, email=MAIL[who])


class _Bus(list):
    def __call__(self, ch, payload):
        self.append((ch, json.loads(payload)))

    def to(self, who):
        return [f for c, f in self if c == f"u:{who}:notifications"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    root.mkdir()
    idx = m.InMemoryMembershipIndex()
    _ws(root, "acme", ALICE, idx, (JOE, "viewer"))
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret=SECRET,
                             global_system_workspace_path=str(root / "_global"),
                             ui_url="https://app.example.test")
    dispatcher = Dispatcher(settings, _Runtime(), _Identity())
    dispatcher.dispatch = lambda inv, **kw: "unit-1"
    bus = _Bus()
    app = create_app(dispatcher, stream_reader=_Reader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store, membership_index=idx, notify_publish=bus,
                     email_subject_lookup=lambda a: {v: k for k, v in MAIL.items()}.get(str(a).lower()))
    yield TestClient(app), store, bus, root
    store.close()


def _h(who: str, **extra) -> dict:
    return {"X-User-Id": who, "X-User-Email": MAIL.get(who, ""), **extra}


def _mine(client, who, **q):
    r = client.get("/api/notifications", params=q, headers=_h(who))
    assert r.status_code == 200, r.text
    return r.json()


def _share(client, store, title="Board prep"):
    store.create(ALICE, name="pchat", title=title)
    r = client.post("/api/sessions/pchat/participants", json={"subject": JOE}, headers=_h(ALICE))
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


# ── chats ─────────────────────────────────────────────────────────────────────────────────────
def test_sharing_a_private_chat_notifies_the_person_added_and_only_them(env):
    client, store, bus, _ = env
    sid = _share(client, store)
    got = _mine(client, JOE)
    assert got["unread"] == 1
    [n] = got["notifications"]
    assert n["kind"] == "chat_shared" and n["object"] == {"kind": "chat", "id": sid, "name": "Board prep"}
    assert n["actor"]["id"] == ALICE and n["text"].startswith("alice") and "Board prep" in n["text"]
    # live, on joe's own channel: the notification, and a rail frame so the chat appears now
    kinds = [f["type"] for f in bus.to(JOE)]
    assert kinds == ["notification", "rail"] and bus.to(JOE)[1] == {"type": "rail", "change": "added", "session": sid}
    # nobody else holds a row: not the actor, not a stranger
    assert _mine(client, ALICE)["notifications"] == [] and _mine(client, DAVE) == {"notifications": [], "unread": 0}
    assert not any(c == f"u:{DAVE}:notifications" for c, _ in bus)


def test_adding_someone_to_an_existing_shared_chat_is_chat_added_and_is_said_once(env):
    client, store, bus, _ = env
    sid = store.create(ALICE, name="team", title="Team")["id"]
    store.add_participant(sid, ALICE, DAVE)
    client.post(f"/api/sessions/{sid}/participants", json={"subject": JOE}, headers=_h(ALICE))
    client.post(f"/api/sessions/{sid}/participants", json={"subject": JOE, "role": "viewer"}, headers=_h(ALICE))
    got = _mine(client, JOE)["notifications"]
    assert [n["kind"] for n in got] == ["chat_added"]


def test_mark_read_and_mark_all_are_the_callers_own(env):
    client, store, _, _ = env
    _share(client, store)
    [n] = _mine(client, JOE)["notifications"]
    r = client.post("/api/notifications/read", json={"ids": [n["id"]]}, headers=_h(DAVE))
    assert r.json() == {"marked": 0, "unread": 0}
    assert _mine(client, JOE)["unread"] == 1
    assert client.post("/api/notifications/read", json={"all": True}, headers=_h(JOE)).json() == {"marked": 1, "unread": 0}
    assert _mine(client, JOE, unread=True)["notifications"] == []
    assert client.post("/api/notifications/read", json={}, headers=_h(JOE)).status_code == 422


def test_a_shared_chat_is_in_the_recipients_rail_under_shared_with_me(env):
    client, store, _, _ = env
    sid = _share(client, store)
    rows = client.get("/api/sessions", headers=_h(JOE)).json()["sessions"]
    row = next(r for r in rows if r["session"] == sid)
    assert row["shared"] is True and row["owner"] == ALICE and row["role"] == "member"
    assert row["owner_name"] == "alice"
    f = client.get("/api/chat/folders", headers=_h(JOE)).json()
    assert [x["id"] for x in f["folders"]][:2] == ["pinned", "shared"]
    assert next(x for x in f["folders"] if x["id"] == "shared") == {
        "id": "shared", "name": "Shared with me", "builtin": True, "collapsed": False, "position": -1}
    assert next(c for c in f["chats"] if c["row"] == f"c:{sid}")["folder"] == "shared"
    # filed like any other chat: into the person's own folder, and back
    mine = client.post("/api/chat/folders", json={"name": "Board"}, headers=_h(JOE)).json()
    assert client.post("/api/chat/move", json={"rows": [f"c:{sid}"], "to": mine["id"]}, headers=_h(JOE)).status_code == 200
    f = client.get("/api/chat/folders", headers=_h(JOE)).json()
    assert next(c for c in f["chats"] if c["row"] == f"c:{sid}")["folder"] == mine["id"]
    # the owner's own copy stays in their own sections, never "shared with me"
    fa = client.get("/api/chat/folders", headers=_h(ALICE)).json()
    assert all(c["folder"] != "shared" for c in fa["chats"] if c["row"] in (f"c:{sid}", "c:pchat"))


def test_shared_with_me_is_built_in_and_cannot_be_renamed_or_dissolved(env):
    client, _, _, _ = env
    r = client.post("/api/chat/folders/update", json={"folder": "shared", "name": "x"}, headers=_h(JOE))
    assert r.status_code == 422
    assert client.post("/api/chat/folders/delete", json={"folder": "shared"}, headers=_h(JOE)).status_code == 422
    assert client.post("/api/chat/folders", json={"name": "Shared with me"}, headers=_h(JOE)).status_code == 422


def test_leaving_takes_the_chat_off_the_rail_and_tells_every_tab(env):
    client, store, bus, _ = env
    sid = _share(client, store)
    client.post("/api/chat/move", json={"rows": [f"c:{sid}"], "to": "pinned"}, headers=_h(JOE))
    bus.clear()
    r = client.delete(f"/api/sessions/{sid}/participants/{JOE}", headers=_h(JOE))
    assert r.status_code == 200 and r.json()["removed"] is True
    assert bus.to(JOE) == [{"type": "rail", "change": "removed", "session": sid}]
    assert all(x["session"] != sid for x in client.get("/api/sessions", headers=_h(JOE)).json()["sessions"])
    assert f"c:{sid}" not in client.get("/api/chat/folders", headers=_h(JOE)).json()["filings"]


def test_being_removed_by_the_owner_tells_the_removed_person(env):
    client, store, bus, _ = env
    sid = _share(client, store)
    bus.clear()
    client.delete(f"/api/sessions/{sid}/participants/{JOE}", headers=_h(ALICE))
    assert {"type": "rail", "change": "removed", "session": sid} in bus.to(JOE)


def test_a_mention_notifies_the_mentioned_participant_and_never_carries_the_message(env):
    client, store, bus, _ = env
    sid = _share(client, store)
    client.post("/api/notifications/read", json={"all": True}, headers=_h(JOE))
    bus.clear()
    r = client.post("/api/chat/submit", json={"prompt": f"@joe {SECRET_LINE}", "session": sid,
                                              "mentions": [JOE, DAVE]}, headers=_h(ALICE))
    assert r.status_code == 200, r.text
    got = _mine(client, JOE, unread=True)["notifications"]
    assert [n["kind"] for n in got] == ["chat_mentioned"]
    assert SECRET_LINE not in json.dumps(got) and "41 dollars" not in json.dumps(bus)
    # dave is not in the chat: a mention of him tells him nothing
    assert _mine(client, DAVE)["notifications"] == []


def test_a_routine_run_in_a_shared_chat_notifies_the_other_people(env):
    client, store, bus, _ = env
    row = store.create(ALICE, name="flow-abc123", title="Inbox digest", flow="user:u_alice/inbox-digest")
    store.add_participant(row["id"], ALICE, JOE)
    r = client.post("/api/chat/submit", json={"prompt": "run", "session": "flow-abc123"},
                    headers=_h(ALICE, **{"x-vexa-flow-regime": "autonomous", "x-internal-secret": SECRET,
                                         "x-vexa-flow-name": "inbox-digest", "x-vexa-flow-scope": "user:u_alice",
                                         "x-vexa-flow-step": "agent_run"}))
    assert r.status_code in (200, 202), r.text
    [n] = _mine(client, JOE)["notifications"]
    assert n["kind"] == "routine_posted" and "inbox-digest" in n["text"] and "Inbox digest" in n["text"]
    assert _mine(client, ALICE)["notifications"] == []


# ── workspaces ────────────────────────────────────────────────────────────────────────────────
def test_inviting_an_account_tells_them_in_the_app_with_the_link(env):
    client, _, bus, _ = env
    r = client.post("/api/workspace/invite", json={"slug": "acme", "email": MAIL[DAVE], "role": "reader"},
                    headers=_h(ALICE))
    assert r.status_code == 200, r.text
    [n] = _mine(client, DAVE)["notifications"]
    assert n["kind"] == "workspace_invited" and n["object"]["kind"] == "workspace"
    assert n["object"]["id"] == "acme" and "/join?i=" in n["object"]["url"]
    assert bus.to(DAVE)[0]["type"] == "notification"


def test_accepting_and_declining_tell_whoever_invited(env):
    client, _, _, root = env
    link = client.post("/api/workspace/invite", json={"slug": "acme", "email": MAIL[DAVE], "role": "reader"},
                       headers=_h(ALICE)).json()["link"]
    token = link.split("i=", 1)[1]
    # a stranger cannot decline an invite made for someone else
    assert client.post("/api/workspace/invites/decline", json={"token": token}, headers=_h(JOE)).status_code == 404
    r = client.post("/api/workspace/invites/decline", json={"token": token}, headers=_h(DAVE))
    assert r.status_code == 200 and r.json()["declined"] is True
    assert [n["kind"] for n in _mine(client, ALICE)["notifications"]] == ["invite_declined"]
    # the declined link no longer works
    assert client.post("/api/workspace/invites/accept", json={"token": token}, headers=_h(DAVE)).status_code == 410
    link2 = client.post("/api/workspace/invite", json={"slug": "acme", "email": MAIL[DAVE], "role": "reader"},
                        headers=_h(ALICE)).json()["link"]
    r = client.post("/api/workspace/invites/accept", json={"token": link2.split("i=", 1)[1]}, headers=_h(DAVE))
    assert r.status_code == 200, r.text
    assert [n["kind"] for n in _mine(client, ALICE)["notifications"]] == ["invite_accepted", "invite_declined"]


def test_adding_an_account_to_a_workspace_tells_them(env):
    client, _, _, root = env
    r = client.post("/api/workspace/members/add", json={"workspace": "acme", "person": MAIL[DAVE], "role": "read"},
                    headers=_h(ALICE))
    assert r.status_code == 200, r.text
    [n] = _mine(client, DAVE)["notifications"]
    assert n["kind"] == "workspace_added" and n["object"]["id"] == "acme"


def test_no_session_store_answers_503(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    settings = load_settings(workspaces_dir=str(tmp_path), redis_url="")
    app = create_app(Dispatcher(settings, _Runtime(), _Identity()), reader=WorkspaceReader(str(tmp_path)),
                     sessions=_Sessions())
    c = TestClient(app)
    assert c.get("/api/notifications", headers=_h(JOE)).status_code == 503
