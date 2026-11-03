"""Join links (#1783): the founder's community flow — a host asks for a link to a workspace, puts it in
a calendar event, and whoever opens it joins; N uses, revocable, optional expiry; the host is told on
each use; joiners become people the host can @-mention. Chats get the same token mechanism.

L2: FastAPI over a sqlite session store, real git workspaces on disk, a list for the redis bus."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import join_links
from control_plane import publish as publish_mod
from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests._session_fakes import MemBlobs

HOST, B, C, D, E = "u_host", "u_bea", "u_cal", "u_dan", "u_eve"
MAIL = {HOST: "host@acme.test", B: "bea@else.test", C: "cal@else.test", D: "dan@else.test",
        E: "eve@else.test"}
SECRET = "internal-s"


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
        yield {"type": "turn-complete"}


def _git(work: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True, check=True)


def _ws(root: Path, slug: str, owner: str, idx) -> None:
    ws = root / slug
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    m.ensure_owner(root, slug, owner, index=idx, email=MAIL[owner], commit_fn=m.policy_commit)


class _Bus(list):
    def __call__(self, ch, payload):
        self.append((ch, json.loads(payload)))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    facts: list = []
    monkeypatch.setattr(publish_mod, "publish",
                        lambda et, sid, refs, timeout=None: facts.append((et, sid, refs)) or True)
    root = tmp_path / "ws"
    root.mkdir()
    idx = m.InMemoryMembershipIndex()
    _ws(root, "dna", HOST, idx)
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret=SECRET,
                             global_system_workspace_path=str(root / "_global"),
                             ui_url="https://app.example.test")
    dispatcher = Dispatcher(settings, _Runtime(), _Identity())
    app = create_app(dispatcher, stream_reader=_Reader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store, membership_index=idx, notify_publish=_Bus(),
                     email_subject_lookup=lambda a: {v: k for k, v in MAIL.items()}.get(str(a).lower()))
    yield TestClient(app), root, facts
    store.close()
    join_links.register_chat_target(None)


def _h(who: str) -> dict:
    return {"X-User-Id": who, "X-User-Email": MAIL.get(who, ""), "x-user-credential-class": "session"}


def _link(client, **body):
    r = client.post("/api/workspace/invite-link", json={"slug": "dna", **body}, headers=_h(HOST))
    assert r.status_code == 201, r.text
    return r.json()


def _token(url: str) -> str:
    return url.rsplit("/join/", 1)[1]


def test_a_link_is_a_join_url_with_an_unguessable_token_stored_only_as_its_hash(env):
    client, root, _ = env
    out = _link(client, max_uses=3)
    assert out["url"].startswith("https://app.example.test/join/")
    tok = _token(out["url"])
    assert len(tok) >= 40
    store = (root / ".invites" / "dna.json").read_text()
    assert tok not in store and m.hash_token(tok) in store
    assert out["max_uses"] == 3 and out["role"] == "contributor"


def test_three_uses_then_a_calm_refusal_and_a_member_reopening_spends_nothing(env):
    client, _, _ = env
    tok = _token(_link(client, max_uses=3, role="member")["url"])
    for who in (B, C, D):
        r = client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(who))
        assert r.status_code == 200, r.text
        assert r.json()["target_kind"] == "workspace" and r.json()["already_member"] is False
    again = client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B))
    assert again.status_code == 200 and again.json()["already_member"] is True
    late = client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(E))
    assert late.status_code == 410
    pv = client.get("/api/join-links/preview", params={"token": tok}).json()
    assert pv == {"valid": False, "reason": "used_up", "target_kind": "workspace"}


def test_the_existing_join_page_route_redeems_the_same_link(env):
    client, _, _ = env
    tok = _token(_link(client)["url"])
    r = client.post("/api/workspace/invites/accept", json={"token": tok}, headers=_h(B))
    assert r.status_code == 200, r.text
    assert m.is_member(env[1], "dna", B) == "contributor"


def test_revoked_and_expired_links_refuse(env):
    client, root, _ = env
    out = _link(client)
    r = client.post("/api/workspace/invite-link/revoke", json={"slug": "dna", "link_id": out["id"]},
                    headers=_h(HOST))
    assert r.status_code == 200, r.text
    assert client.post("/api/join-links/redeem", json={"token": _token(out["url"])},
                       headers=_h(B)).status_code == 410
    links = client.get("/api/workspace/invite-links", params={"slug": "dna"}, headers=_h(HOST)).json()["links"]
    assert [l["state"] for l in links] == ["revoked"]
    assert "hash" not in links[0] and "url" not in links[0]
    # expiry
    minted = join_links.mint(root, "workspace", "dna", role="contributor", created_by=HOST,
                             max_uses=2, expires_in_sec=60, now=0)
    assert client.post("/api/join-links/redeem", json={"token": minted.token}, headers=_h(B)).status_code == 410


def test_only_the_owner_mints_a_link(env):
    client, _, _ = env
    tok = _token(_link(client)["url"])
    client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B))
    r = client.post("/api/workspace/invite-link", json={"slug": "dna"}, headers=_h(B))
    assert r.status_code == 403


def test_each_use_notifies_the_host_and_publishes_the_fact_for_mail(env):
    client, _, facts = env
    tok = _token(_link(client)["url"])
    client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B))
    client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B))   # idempotent
    got = client.get("/api/notifications", headers=_h(HOST)).json()
    rows = [n for n in got["notifications"] if n["kind"] == "workspace_joined_via_link"]
    assert len(rows) == 1
    assert rows[0]["actor"]["id"] == B and "via your link" in rows[0]["text"]
    joined = [f for f in facts if f[0] == "workspace.joined_via_link"]
    assert len(joined) == 1
    refs = joined[0][2]
    assert refs["uid"] == HOST and refs["joiner"] == B and refs["joiner_email"] == MAIL[B]
    assert refs["workspace"] == "dna"


def test_people_who_joined_by_link_are_in_the_hosts_picker_and_the_host_in_theirs(env):
    client, _, _ = env
    tok = _token(_link(client)["url"])
    client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B))
    r = client.get("/api/people/mentionable", headers=_h(HOST))
    assert r.status_code == 200, r.text
    people = r.json()["people"]
    assert any(p["subject"] == B and p["email"] == MAIL[B] for p in people)
    theirs = client.get("/api/people/mentionable", headers=_h(B)).json()["people"]
    assert any(p["subject"] == HOST for p in theirs)


def test_a_marker_in_an_event_body_resolves_to_its_workspace(env):
    client, _, _ = env
    url = _link(client)["url"]
    body = f"Agenda\n\nJoin the workspace: {url}\nMeet: https://meet.google.com/abc-defg-hij"
    toks = join_links.markers(body)
    assert toks == [_token(url)]
    pv = client.get("/api/join-links/preview", params={"token": toks[0]}).json()
    assert pv["valid"] and pv["target_kind"] == "workspace" and pv["workspace_id"] == "dna"
    assert join_links.markers("https://app.example.test/join?i=" + toks[0]) == toks


def test_chat_links_use_the_same_mechanism_once_the_chat_domain_plugs_in(env):
    client, _, _ = env
    r = client.post("/api/join-links", json={"target_kind": "chat", "target_id": "c1"}, headers=_h(HOST))
    assert r.status_code == 501
    members: set = set()
    join_links.register_chat_target(join_links.ChatTarget(
        may_link=lambda s, c: s == HOST and c == "c1",
        is_in=lambda s, c: s in members,
        join=lambda s, e, c, role: members.add(s),
        title=lambda c: "Board prep"))
    assert client.post("/api/join-links", json={"target_kind": "chat", "target_id": "c1"},
                       headers=_h(B)).status_code == 404
    out = client.post("/api/join-links", json={"target_kind": "chat", "target_id": "c1", "max_uses": 1},
                      headers=_h(HOST)).json()
    tok = _token(out["url"])
    pv = client.get("/api/join-links/preview", params={"token": tok}).json()
    assert pv["target_kind"] == "chat" and pv["name"] == "Board prep"
    r = client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B))
    assert r.status_code == 200 and r.json()["chat_id"] == "c1" and B in members
    assert client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(C)).status_code == 410
    assert client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B)).status_code == 200
