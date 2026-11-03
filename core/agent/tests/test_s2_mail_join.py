"""PRD 0.13.4 S2 (E1) — mail and join, agent-api half.

* A restricted invite to one address that this deployment MAILED (delivery ``delivered``) signs that
  address in with one click — the mailbox is the proof — and the inviter is never handed that link.
* The sign-in half is spent once, on the terminal's POST (``/internal/invites/signin-claim``), never
  by a GET: previewing the link any number of times (a mail scanner) changes nothing.
* An invite can name a room of the workspace; the join lands the person in it.
* M2: an invite notification stores the invite's id, never its token, and old rows are rewritten.
* M6: the workspace lock can be shared across replicas (Redis).

L2: FastAPI over a sqlite session store and real git workspaces on disk; the mail carrier and the
delivery answer are stubbed (no mail is sent anywhere)."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient

from control_plane import publish as publish_mod
from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.notifications import Notifier
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests._session_fakes import MemBlobs

ALICE, JOE, DAVE = "u_alice", "u_joe", "u_dave"
MAIL = {ALICE: "alice@acme.test", JOE: "joe@acme.test", DAVE: "dave@other.test"}
NEW = "valeriia@fresh.test"
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


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    root.mkdir()
    idx = m.InMemoryMembershipIndex()
    _ws(root, "acme", ALICE, idx, (JOE, "contributor"))
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret=SECRET,
                             global_system_workspace_path=str(root / "_global"),
                             ui_url="https://app.example.test")
    dispatcher = Dispatcher(settings, _Runtime(), _Identity())
    dispatcher.dispatch = lambda inv, **kw: "unit-1"
    lookup = {v: k for k, v in MAIL.items()}
    app = create_app(dispatcher, stream_reader=_Reader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store, membership_index=idx,
                     notify_publish=lambda ch, payload: None,
                     email_subject_lookup=lambda a: lookup.get(str(a).lower()))
    sent: list = []
    monkeypatch.setattr(publish_mod, "publish_invite", lambda fact, **kw: (sent.append(fact), True)[1])
    monkeypatch.setattr(publish_mod, "mail_delivery", lambda **kw: "delivered")
    yield TestClient(app), store, root, sent
    store.close()


def _h(who: str, email: str = "", **extra) -> dict:
    return {"X-User-Id": who, "X-User-Email": email or MAIL.get(who, ""), "x-user-credential-class": "session", **extra}


def _token(link: str) -> str:
    return link.split("i=", 1)[1]


def _invite(client, email=NEW, **body):
    r = client.post("/api/workspace/invite", json={"slug": "acme", "email": email, **body},
                    headers=_h(ALICE))
    assert r.status_code == 200, r.text
    return r.json()


def _claim(client, token, secret=SECRET):
    return client.post("/internal/invites/signin-claim", json={"token": token},
                       headers={"X-Internal-Secret": secret} if secret else {})


# ── the mailed link signs its one address in, and only that link does ────────────────────────────
def test_a_mailed_invite_signs_in_and_the_inviter_never_holds_that_link(env):
    client, _, _, sent = env
    out = _invite(client)
    assert out["delivery"] == "mailed" and out["signs_in"] is True
    assert out["link"] == "", "the inviter (and their agent) must not hold a link that signs somebody in"
    assert "join?i=" not in json.dumps(out)
    [fact] = sent
    assert fact["email"] == NEW and fact["link"].startswith("https://app.example.test/join?i=")


def test_a_mail_scanner_opening_the_link_changes_nothing(env):
    """The scanner-GET test: preview is a read, any number of times; the sign-in stays unspent and
    the invite unused until the POST behind the button."""
    client, _, root, sent = env
    _invite(client)
    tok = _token(sent[0]["link"])
    for _ in range(5):                                  # Defender, SkypeUriPreview, a person, …
        r = client.get("/api/workspace/invites/preview", params={"token": tok})
        assert r.status_code == 200 and r.json()["valid"] is True and r.json()["signin"] is True
    _, rec = m.find_invite(root, tok)
    assert rec["uses"] == 0 and not rec.get("signin_claimed")
    got = _claim(client, tok)
    assert got.status_code == 200, got.text
    assert got.json()["email"] == NEW and got.json()["workspace_id"] == "acme"


def test_the_sign_in_half_is_spent_once(env):
    client, _, _, sent = env
    _invite(client)
    tok = _token(sent[0]["link"])
    assert _claim(client, tok).status_code == 200
    again = _claim(client, tok)
    assert again.status_code == 410
    # and the preview no longer offers the one-click sign-in
    assert client.get("/api/workspace/invites/preview", params={"token": tok}).json()["signin"] is False


def test_the_claim_is_internal_tier_only(env):
    client, _, _, sent = env
    _invite(client)
    tok = _token(sent[0]["link"])
    assert _claim(client, tok, secret="").status_code == 403
    assert _claim(client, tok, secret="wrong").status_code == 403
    assert _claim(client, tok).status_code == 200


def test_captured_mail_never_signs_in(env, monkeypatch):
    client, _, _, sent = env
    monkeypatch.setattr(publish_mod, "mail_delivery", lambda **kw: "captured")
    out = _invite(client)
    assert out["delivery"] == "captured" and out["signs_in"] is False and out["link"]
    assert _claim(client, _token(out["link"])).status_code == 409


def test_a_link_handed_to_the_inviter_never_signs_in(env, monkeypatch):
    client, _, _, _ = env
    monkeypatch.setattr(publish_mod, "publish_invite", lambda fact, **kw: False)   # carrier down
    out = _invite(client)
    assert out["delivery"] == "link" and out["link"] and out["signs_in"] is False
    assert _claim(client, _token(out["link"])).status_code == 409


def test_the_sign_in_window_closes(env, monkeypatch):
    client, _, root, sent = env
    _invite(client)
    tok = _token(sent[0]["link"])
    later = time.time() + m.SIGNIN_TTL_SEC + 60
    with pytest.raises(m.MembershipError) as exc:
        m.claim_invite_signin(root, tok, now=later)
    assert exc.value.status == 410


def test_after_the_claim_the_ordinary_accept_joins_as_the_verified_address(env):
    client, _, root, sent = env
    _invite(client)
    tok = _token(sent[0]["link"])
    assert _claim(client, tok).status_code == 200
    r = client.post("/api/workspace/invites/accept", json={"token": tok}, headers=_h("u_new", NEW))
    assert r.status_code == 200, r.text
    assert m.is_member(root, "acme", "u_new") == "contributor"
    # a different address holding the link gets nothing
    r = client.post("/api/workspace/invites/accept", json={"token": tok}, headers=_h("u_x", "x@evil.test"))
    assert r.status_code in (403, 410)


# ── a room ───────────────────────────────────────────────────────────────────────────────────────
def _room(store, owner=ALICE, ws="acme", title="test"):
    row = store.create(owner, name=None, title=title)
    store.put_room(row["id"], workspace=ws, visibility="closed")
    return row["id"]


def test_an_invite_into_a_room_names_it_and_lands_in_it(env):
    client, store, root, sent = env
    rid = _room(store)
    out = _invite(client, room=rid)
    assert out["room"] == {"id": rid, "title": "test"}
    assert "test in acme" in out["said"].lower() or "test in" in out["said"]
    assert sent[0]["room"] == "test"
    tok = _token(sent[0]["link"])
    pv = client.get("/api/workspace/invites/preview", params={"token": tok}).json()
    assert pv["room"] == {"id": rid, "title": "test"}
    r = client.post("/api/workspace/invites/accept", json={"token": tok}, headers=_h("u_new", NEW))
    assert r.status_code == 200, r.text
    assert r.json()["room"]["id"] == rid
    assert store.role_of(rid, "u_new") == "member"


def test_a_room_of_another_workspace_or_one_the_inviter_is_not_in_is_refused(env):
    client, store, root, _ = env
    other = _room(store, owner=JOE)                    # Joe's room: Alice is not in it
    r = client.post("/api/workspace/invite", json={"slug": "acme", "email": NEW, "room": other},
                    headers=_h(ALICE))
    assert r.status_code == 422 and r.json()["detail"]["refused"] == "not_a_room_here"


# ── M2: no token at rest in a notification row ───────────────────────────────────────────────────
def test_an_invite_notification_stores_the_invite_id_not_the_token(env):
    client, store, _, _ = env
    out = _invite(client, email=MAIL[DAVE])            # an account here: told in the app
    assert out["internal"] is True and out["link"]
    with store._lock:
        rows = store._x("SELECT url FROM agent_session_notification WHERE recipient = ?", (DAVE,)).fetchall()
    assert rows and all(str(u).startswith("invite:acme/") for (u,) in rows)
    assert all(_token(out["link"]) not in str(u) for (u,) in rows)
    # the act resolves the id server-side, bound to Dave's verified address
    [n] = client.get("/api/notifications", headers=_h(DAVE)).json()["notifications"]
    r = client.post("/api/notifications/act", json={"id": n["id"], "action": "accept"}, headers=_h(DAVE))
    assert r.status_code == 200, r.text
    assert m.is_member(env[2], "acme", DAVE) is not None


def test_old_rows_holding_a_token_are_rewritten(env):
    client, store, root, _ = env
    minted = m.mint_invite(root, "acme", role="viewer", created_by=ALICE, mode="restricted",
                           allowed_emails=[MAIL[DAVE]])
    n = Notifier(store)
    n.emit([DAVE], kind="workspace_invited", actor=ALICE, actor_name="Alice", object_kind="workspace",
           object_id="acme", object_name="Acme", url=f"https://app.example.test/join?i={minted.token}")
    n.emit([JOE], kind="workspace_invited", actor=ALICE, actor_name="Alice", object_kind="workspace",
           object_id="acme", object_name="Acme", url="https://app.example.test/join?i=gone-token")

    def resolve(tok):
        found = m.find_invite(root, tok)
        return (found[0], found[1]["id"]) if found else None

    assert n.scrub_invite_tokens(resolve) == 2
    with store._lock:
        urls = dict(store._x("SELECT recipient, url FROM agent_session_notification").fetchall())
    assert urls[DAVE] == f"invite:acme/{minted.id}" and urls[JOE] is None
    assert n.scrub_invite_tokens(resolve) == 0                       # idempotent


# ── M6: the shared lock ──────────────────────────────────────────────────────────────────────────
def test_the_redis_lock_serialises_two_holders_and_is_taken_once_per_thread():
    r = fakeredis.FakeRedis(decode_responses=True)
    order: list = []
    m.set_distributed_lock(lambda ws: m.RedisWorkspaceLock(r, ws, wait_s=2.0))
    try:
        with m._ws_lock("acme"):
            assert r.get("vexa:ws-lock:acme")
            with m._ws_lock("acme"):                  # re-entrant: no self-deadlock
                pass
            assert r.get("vexa:ws-lock:acme"), "the inner exit must not release the outer hold"
            # another holder (a second replica) waits, then refuses with 503
            other = m.RedisWorkspaceLock(r, "acme", wait_s=0.1)
            with pytest.raises(m.MembershipError) as exc:
                other.__enter__()
            assert exc.value.status == 503
        assert r.get("vexa:ws-lock:acme") is None

        def hold(tag):
            with m._ws_lock("beta"):
                order.append(f"{tag}+")
                time.sleep(0.05)
                order.append(f"{tag}-")

        ts = [threading.Thread(target=hold, args=(t,)) for t in "ab"]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert order in (["a+", "a-", "b+", "b-"], ["b+", "b-", "a+", "a-"])
    finally:
        m.set_distributed_lock(None)


# ── the sign-in window is a company policy line (`invite_signin_days`, default 7) ───────────────
def _policy(root, text):
    g = root / "_global"
    g.mkdir(parents=True, exist_ok=True)
    (g / "POLICIES.md").write_text(f"---\n{text}\n---\n# Policies\n")


def test_the_window_defaults_to_seven_days(env):
    client, _, root, sent = env
    _invite(client)
    _, rec = m.find_invite(root, _token(sent[0]["link"]))
    assert 7 * 86400 - 120 <= rec["signin_until"] - time.time() <= 7 * 86400


def test_the_window_follows_the_policy_line(env):
    client, _, root, sent = env
    _policy(root, "invite_signin_days: 2")
    _invite(client)
    _, rec = m.find_invite(root, _token(sent[0]["link"]))
    assert 2 * 86400 - 120 <= rec["signin_until"] - time.time() <= 2 * 86400


def test_zero_days_means_a_mailed_link_never_signs_in(env):
    client, _, root, sent = env
    _policy(root, "invite_signin_days: 0")
    out = _invite(client)
    assert out["delivery"] == "mailed" and out["signs_in"] is False and out["link"]
    assert _claim(client, _token(sent[0]["link"])).status_code == 409


def test_an_out_of_range_line_keeps_the_default(env):
    client, _, root, sent = env
    _policy(root, "invite_signin_days: 400")
    _invite(client)
    _, rec = m.find_invite(root, _token(sent[0]["link"]))
    assert rec["signin_until"] - time.time() > 6 * 86400
