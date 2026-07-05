"""Lane M — membership + invites + roles (the access layer for shared workspaces).

Offline L2 tests over fakes (no docker, no runtime, no DB): the git-backed authoritative store
(policy/members.json + policy/invites.json), the users.data.memberships[] index mirror (an in-memory
fake ``MembershipIndex``), the HTTP surface (invite mint→accept→membership, expiry, max_uses,
double-accept idempotency, revoke, role flip, role gating), the reserved/own-private guard, and the
policy/ PLATFORM-WRITE-ONLY turn-commit guard.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


# ── fakes ────────────────────────────────────────────────────────────────────────────────────────
class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _init_ws(root: Path, workspace_id: str) -> Path:
    """A real git workspace dir (so policy_commit + the guard exercise real git)."""
    ws = root / workspace_id
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _client(root: Path, index=None):
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=index or m.InMemoryMembershipIndex(),
    ))


def _h(subject: str) -> dict:
    return {"X-User-Id": subject}


# ── the store: both writes, is_member, require_role ───────────────────────────────────────────────
def test_ensure_owner_writes_both_stores(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "wsA", "u1", index=idx, commit_fn=m.policy_commit)
    # git file authoritative
    members = json.loads((tmp_path / "wsA" / m.MEMBERS_FILE).read_text())
    assert members == [{"subject": "u1", "role": "owner", "added_by": "u1",
                        "added_at": members[0]["added_at"]}]
    # index mirror
    assert idx.list("u1") == [{"workspace_id": "wsA", "role": "owner",
                               "added_at": members[0]["added_at"]}]
    # committed to git (policy/ is present in HEAD)
    assert "policy/members.json" in _git(tmp_path / "wsA", "ls-files")


def test_is_member_and_require_role_lattice(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "wsA", "owner1", index=idx)
    m.grant_membership(tmp_path, "wsA", "viewer1", "viewer", added_by="owner1", index=idx)
    m.grant_membership(tmp_path, "wsA", "contrib1", "contributor", added_by="owner1", index=idx)

    assert m.is_member(tmp_path, "wsA", "owner1") == "owner"
    assert m.is_member(tmp_path, "wsA", "contrib1") == "contributor"
    assert m.is_member(tmp_path, "wsA", "nobody") is None

    # owner >= contributor >= viewer
    assert m.require_role(tmp_path, "wsA", "owner1", "owner") == "owner"
    assert m.require_role(tmp_path, "wsA", "contrib1", "viewer") == "contributor"
    with pytest.raises(m.MembershipError):
        m.require_role(tmp_path, "wsA", "viewer1", "contributor")
    with pytest.raises(m.MembershipError):
        m.require_role(tmp_path, "wsA", "nobody", "viewer")


# ── invites: mint → accept → membership; expiry; max_uses; idempotency; revoke ────────────────────
def test_mint_stores_only_hash_and_returns_token_once(tmp_path):
    _init_ws(tmp_path, "wsA")
    minted = m.mint_invite(tmp_path, "wsA", role="contributor", created_by="owner1")
    invites = json.loads((tmp_path / "wsA" / m.INVITES_FILE).read_text())
    assert len(invites) == 1
    assert invites[0]["hash"] == m.hash_token(minted.token)
    assert "token" not in invites[0]  # only the hash is persisted
    assert list_invites_has_no_hash(tmp_path)


def list_invites_has_no_hash(root: Path) -> bool:
    return all("hash" not in i for i in m.list_invites(root, "wsA"))


def test_accept_grants_membership_both_stores(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    minted = m.mint_invite(tmp_path, "wsA", role="viewer", created_by="owner1")
    res = m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u2", index=idx)
    assert res == {"workspace_id": "wsA", "role": "viewer", "already_member": False}
    assert m.is_member(tmp_path, "wsA", "u2") == "viewer"
    assert idx.list("u2")[0]["workspace_id"] == "wsA"
    # a use was consumed
    assert json.loads((tmp_path / "wsA" / m.INVITES_FILE).read_text())[0]["uses"] == 1


def test_accept_expired_invite_fails(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    minted = m.mint_invite(tmp_path, "wsA", role="viewer", created_by="o", expires_in_sec=100, now=1000.0)
    with pytest.raises(m.MembershipError) as e:
        m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u2", index=idx, now=2000.0)
    assert e.value.status == 410


def test_max_uses_enforced(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    minted = m.mint_invite(tmp_path, "wsA", role="viewer", created_by="o", max_uses=1)
    m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u2", index=idx)
    with pytest.raises(m.MembershipError) as e:
        m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u3", index=idx)
    assert e.value.status == 410  # fully used


def test_double_accept_is_idempotent(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    minted = m.mint_invite(tmp_path, "wsA", role="viewer", created_by="o", max_uses=1)
    m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u2", index=idx)
    # same user accepts again — no error, still ONE membership, use NOT re-consumed
    res = m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u2", index=idx)
    assert res["already_member"] is True
    members = json.loads((tmp_path / "wsA" / m.MEMBERS_FILE).read_text())
    assert [x["subject"] for x in members].count("u2") == 1
    assert json.loads((tmp_path / "wsA" / m.INVITES_FILE).read_text())[0]["uses"] == 1


def test_revoked_invite_rejected(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    minted = m.mint_invite(tmp_path, "wsA", role="viewer", created_by="o")
    invite_id = json.loads((tmp_path / "wsA" / m.INVITES_FILE).read_text())[0]["id"]
    m.revoke_invite(tmp_path, "wsA", invite_id)
    with pytest.raises(m.MembershipError) as e:
        m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u2", index=idx)
    assert e.value.status == 410


# ── role flip + remove ────────────────────────────────────────────────────────────────────────────
def test_role_flip(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "wsA", "o", index=idx)
    m.grant_membership(tmp_path, "wsA", "u2", "viewer", added_by="o", index=idx)
    m.set_role(tmp_path, "wsA", "u2", "contributor", changed_by="o", index=idx)
    assert m.is_member(tmp_path, "wsA", "u2") == "contributor"
    assert idx.list("u2")[0]["role"] == "contributor"


def test_cannot_remove_last_owner(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "wsA", "o", index=idx)
    with pytest.raises(m.MembershipError) as e:
        m.remove_member(tmp_path, "wsA", "o", index=idx)
    assert e.value.status == 409
    with pytest.raises(m.MembershipError):
        m.set_role(tmp_path, "wsA", "o", "viewer", changed_by="o", index=idx)


def test_remove_member(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "wsA", "o", index=idx)
    m.grant_membership(tmp_path, "wsA", "u2", "viewer", added_by="o", index=idx)
    m.remove_member(tmp_path, "wsA", "u2", index=idx)
    assert m.is_member(tmp_path, "wsA", "u2") is None
    assert idx.list("u2") == []


# ── reserved / own-private guard ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("slug", ["sys", "_system", "seed", ".attached", ".hidden"])
def test_reserved_slugs_not_shareable(tmp_path, slug):
    with pytest.raises(m.MembershipError) as e:
        m.assert_shareable(tmp_path, slug)
    assert e.value.status == 403


def test_mint_on_reserved_refused(tmp_path):
    with pytest.raises(m.MembershipError) as e:
        m.mint_invite(tmp_path, "sys", role="viewer", created_by="o")
    assert e.value.status == 403


# ── HTTP surface: role gating + full mint→accept flow ─────────────────────────────────────────────
def test_api_role_gating_viewer_cannot_invite_owner_can(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "wsA", "owner1", index=idx)
    m.grant_membership(tmp_path, "wsA", "viewer1", "viewer", added_by="owner1", index=idx)
    c = _client(tmp_path, idx)

    # viewer cannot invite (needs contributor)
    r = c.post("/api/workspace/invites", headers=_h("viewer1"),
               json={"workspace_id": "wsA", "role": "viewer"})
    assert r.status_code == 403

    # owner can
    r = c.post("/api/workspace/invites", headers=_h("owner1"),
               json={"workspace_id": "wsA", "role": "contributor"})
    assert r.status_code == 201
    assert r.json()["token"]


def test_api_full_invite_accept_flow(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "wsA", "owner1", index=idx)
    c = _client(tmp_path, idx)

    minted = c.post("/api/workspace/invites", headers=_h("owner1"),
                    json={"workspace_id": "wsA", "role": "contributor", "max_uses": 2}).json()
    # a DIFFERENT user accepts by token alone (no workspace id in the body — resolved by hash)
    r = c.post("/api/workspace/invites/accept", headers=_h("u2"), json={"token": minted["token"]})
    assert r.status_code == 200 and r.json()["role"] == "contributor"

    # the new contributor now shows in the members list + can list (contributor read)
    members = c.get("/api/workspace/members", headers=_h("u2"), params={"workspace_id": "wsA"})
    assert members.status_code == 200
    assert "u2" in [x["subject"] for x in members.json()["members"]]

    # "shared with me" reflects the index
    shared = c.get("/api/workspace/shared", headers=_h("u2")).json()["memberships"]
    assert shared[0]["workspace_id"] == "wsA"


def test_api_role_flip_and_remove_owner_only(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "wsA", "owner1", index=idx)
    m.grant_membership(tmp_path, "wsA", "u2", "viewer", added_by="owner1", index=idx)
    m.grant_membership(tmp_path, "wsA", "contrib1", "contributor", added_by="owner1", index=idx)
    c = _client(tmp_path, idx)

    # contributor cannot flip roles (owner-only)
    assert c.post("/api/workspace/members/u2/role", headers=_h("contrib1"),
                  params={"workspace_id": "wsA"}, json={"role": "contributor"}).status_code == 403
    # owner can
    r = c.post("/api/workspace/members/u2/role", headers=_h("owner1"),
               params={"workspace_id": "wsA"}, json={"role": "contributor"})
    assert r.status_code == 200 and r.json()["role"] == "contributor"
    # owner removes
    assert c.delete("/api/workspace/members/u2", headers=_h("owner1"),
                    params={"workspace_id": "wsA"}).status_code == 200
    assert m.is_member(tmp_path, "wsA", "u2") is None


def test_api_accept_unknown_token_404(tmp_path):
    _init_ws(tmp_path, "wsA")
    c = _client(tmp_path)
    r = c.post("/api/workspace/invites/accept", headers=_h("u2"), json={"token": "nope"})
    assert r.status_code == 404


# ── policy/ PLATFORM-WRITE-ONLY guard (worker turn-commit path) ────────────────────────────────────
def test_policy_write_guard_reverts_agent_write(tmp_path):
    """An agent turn that writes under policy/ has that write REVERTED before the turn commits; a
    non-policy write in the same turn still commits."""
    from llm.ports import run_harness_turn

    ws = _init_ws(tmp_path, "wsA")
    # seed an authoritative members.json (a committed policy/ file the agent must not clobber)
    m.ensure_owner(tmp_path, "wsA", "owner1", index=m.InMemoryMembershipIndex(), commit_fn=m.policy_commit)
    original = (ws / m.MEMBERS_FILE).read_text()

    class _Harness:
        def run_turn(self, work, prompt, *, allowed_tools, session, model, mcp_config=None):
            # the "agent" writes a legit note AND tampers with policy/
            (Path(work) / "note.md").write_text("agent note\n")
            (Path(work) / m.MEMBERS_FILE).write_text('[{"subject":"attacker","role":"owner"}]\n')
            (Path(work) / "policy" / "evil.json").write_text("{}\n")  # untracked policy/ add
            yield {"type": "done", "reply": "did work", "sessionId": "s1", "ok": True}

    events = list(run_harness_turn(ws, "go", _Harness()))
    kinds = [e["type"] for e in events]
    assert "policy-reverted" in kinds
    reverted = next(e for e in events if e["type"] == "policy-reverted")["paths"]
    assert m.MEMBERS_FILE in reverted
    assert "policy/evil.json" in reverted

    # policy/members.json restored to the committed version; the tamper is gone
    assert (ws / m.MEMBERS_FILE).read_text() == original
    assert not (ws / "policy" / "evil.json").exists()
    # the legit non-policy write DID commit
    assert (ws / "note.md").exists()
    assert "note.md" in _git(ws, "show", "--name-only", "--format=", "HEAD")
    # the committed tree has the ORIGINAL members.json, not the attacker's
    committed = _git(ws, "show", "HEAD:policy/members.json")
    assert "attacker" not in committed


# ── invite ACCESS MODES (AMENDMENT 5): open vs email-restricted ───────────────────────────────────
def test_open_mode_lets_anyone_with_link_in(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    minted = m.mint_invite(tmp_path, "wsA", role="viewer", created_by="o", mode="open")
    # no email needed for open mode
    res = m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u2", index=idx)
    assert res["role"] == "viewer"
    assert m.is_member(tmp_path, "wsA", "u2") == "viewer"


def test_restricted_requires_allowed_emails_at_mint(tmp_path):
    _init_ws(tmp_path, "wsA")
    with pytest.raises(m.MembershipError) as e:
        m.mint_invite(tmp_path, "wsA", role="viewer", created_by="o", mode="restricted", allowed_emails=[])
    assert e.value.status == 400


def test_restricted_refuses_non_listed_admits_listed(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    minted = m.mint_invite(tmp_path, "wsA", role="contributor", created_by="o",
                           mode="restricted", allowed_emails=["Alice@Vexa.ai"])
    # a non-listed authenticated user is refused even with a valid link + auth
    with pytest.raises(m.MembershipError) as e:
        m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u_bob",
                        subject_email="bob@vexa.ai", index=idx)
    assert e.value.status == 403
    # the use was NOT consumed by the refused attempt
    assert json.loads((tmp_path / "wsA" / m.INVITES_FILE).read_text())[0]["uses"] == 0
    # a listed user (case-insensitive match) is admitted
    res = m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u_alice",
                          subject_email="alice@vexa.ai", index=idx)
    assert res["role"] == "contributor"
    assert m.is_member(tmp_path, "wsA", "u_alice") == "contributor"


def test_restricted_missing_email_refused(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    minted = m.mint_invite(tmp_path, "wsA", role="viewer", created_by="o",
                           mode="restricted", allowed_emails=["alice@vexa.ai"])
    with pytest.raises(m.MembershipError) as e:
        m.accept_invite(tmp_path, "wsA", token=minted.token, subject="u2", index=idx)  # no email
    assert e.value.status == 403


def test_api_restricted_invite_checks_x_user_email(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "wsA", "owner1", index=idx)
    c = _client(tmp_path, idx)
    minted = c.post("/api/workspace/invites", headers=_h("owner1"),
                    json={"workspace_id": "wsA", "role": "viewer", "mode": "restricted",
                          "allowed_emails": ["alice@vexa.ai"]}).json()
    assert minted["mode"] == "restricted"
    # wrong email → 403
    bad = c.post("/api/workspace/invites/accept",
                 headers={"X-User-Id": "u_bob", "X-User-Email": "bob@vexa.ai"},
                 json={"token": minted["token"]})
    assert bad.status_code == 403
    # right email → 200
    ok = c.post("/api/workspace/invites/accept",
                headers={"X-User-Id": "u_alice", "X-User-Email": "alice@vexa.ai"},
                json={"token": minted["token"]})
    assert ok.status_code == 200 and ok.json()["role"] == "viewer"
