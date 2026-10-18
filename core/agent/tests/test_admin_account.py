"""THE ADMIN ACCOUNT — the one identity that controls `_global`, and that has nothing else.

Founder ruling 2026-10-11: *"let's change so that admin is a special account that has global only and
this being the only one that has control to global"*. The deny tests, in three groups:

  1. WHO — agent-api's one reader (`global_layer.is_admin_account`) believes identity's
     `admin_account` and nothing else: not the instance admin's `is_admin`, not the retired
     `VEXA_GLOBAL_ADMIN_SUBJECTS`, and nobody when identity cannot answer.
  2. EVERYDAY ACCOUNTS — the instance admin's included — never write `_global`: files, company
     pages, its reset, its acceptance, a chat aimed at it, its mount.
  3. THE ADMIN ACCOUNT — no desk, no workspaces, no meetings: refused at the doors that would give it
     one (`admin_account_scope.REFUSED`), at the workspace door (`_read_target`), and at the chat
     target; its mount table is `_global` + `_system`.

Plus: nothing configured → nobody writes `_global`. L2: a real FastAPI app over fakes.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient

from control_plane import admin_account_scope, global_layer
from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher, build_mount_set
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests import admin_accounts
from tests.test_api import _FakeIdentity, _FakeRuntime

ADMIN_ACCOUNT = "u_company_admin"
FOUNDER = "u_founder"          # an everyday account that IS the instance admin
MEMBER = "u_priya"


# ── 1. WHO ───────────────────────────────────────────────────────────────────────────────────────

class _Secret:
    def __init__(self, v):
        self.v = v

    def get_secret_value(self):
        return self.v


def _identity_settings(url="http://admin-api.test", secret="s"):
    from types import SimpleNamespace
    return SimpleNamespace(admin_api_url=url, internal_api_secret=_Secret(secret))


@pytest.fixture
def identity(monkeypatch):
    """admin-api's `/internal/users/{id}/is-admin`, answered from a dict per subject."""
    answers: dict = {}
    global_layer._CACHE.clear()

    def fake(url, secret, path, timeout=6.0):
        subject = path.split("/")[3]
        if subject not in answers:
            raise urllib.error.URLError("unreachable")
        return answers[subject]
    monkeypatch.setattr(global_layer, "_admin_api", fake)
    yield answers
    global_layer._CACHE.clear()


def test_the_admin_account_is_identitys_answer_and_nothing_else(identity):
    s = _identity_settings()
    identity[ADMIN_ACCOUNT] = {"is_admin": False, "admin_account": True}
    identity[FOUNDER] = {"is_admin": True, "admin_account": False}
    identity[MEMBER] = {"is_admin": True}                    # an older identity: no field at all
    assert global_layer.is_admin_account(s, ADMIN_ACCOUNT) is True
    assert global_layer.is_admin_account(s, FOUNDER) is False
    assert global_layer.is_admin(s, FOUNDER) is True        # still the instance admin, for Settings
    assert global_layer.is_admin_account(s, MEMBER) is False


def test_identity_unreachable_or_unconfigured_means_nobody(identity, monkeypatch):
    assert global_layer.is_admin_account(_identity_settings(), "u_nobody") is False   # unreachable
    identity[ADMIN_ACCOUNT] = {"admin_account": True}
    assert global_layer.is_admin_account(_identity_settings(url=""), ADMIN_ACCOUNT) is False
    assert global_layer.is_admin_account(_identity_settings(secret=""), ADMIN_ACCOUNT) is False


def test_the_retired_subject_list_grants_nothing(identity, monkeypatch):
    """`VEXA_GLOBAL_ADMIN_SUBJECTS` was a raw id list in agent-api; it is gone, so naming somebody
    there must not make them a writer — identity is the one writer of this answer."""
    monkeypatch.setenv("VEXA_GLOBAL_ADMIN_SUBJECTS", FOUNDER)
    identity[FOUNDER] = {"is_admin": True, "admin_account": False}
    s = load_settings(admin_api_url="http://admin-api.test", internal_api_secret="s", redis_url="")
    assert not hasattr(s, "global_admin_subjects")
    assert global_layer.is_admin_account(s, FOUNDER) is False


# ── the app, over fakes ──────────────────────────────────────────────────────────────────────────

class _Reader:
    def read(self, unit_id, resume=None):
        yield {"type": "turn-complete"}


@pytest.fixture
def stack(tmp_path, monkeypatch, fake_admin_accounts):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    # the founder is the INSTANCE admin — a person; it must not matter here
    monkeypatch.setattr(global_layer, "is_admin", lambda settings, subject: str(subject) == FOUNDER)
    return {"root": root, "runtime": _FakeRuntime()}


def _client(stack, *, admin_account: str = ADMIN_ACCOUNT):
    admin_accounts.name(admin_account)
    settings = load_settings(workspaces_dir=str(stack["root"]),
                             global_system_workspace_path=str(stack["root"] / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    return TestClient(create_app(Dispatcher(settings, stack["runtime"], _FakeIdentity()),
                                 stream_reader=_Reader(), reader=WorkspaceReader(str(stack["root"])),
                                 sessions=_Sessions(), membership_index=m.InMemoryMembershipIndex()))


def _h(subject):
    return {"X-User-Id": subject, "x-user-email": f"{subject}@example.test"}


def _company_writes(c, subject):
    """Every door that writes `_global`, as `subject`. Returns {door: status}."""
    out = {}
    out["file"] = c.put("/api/workspace/file", headers=_h(subject), json={
        "path": "POLICIES.md", "content": "# Policies\n", "slug": "_global"}).status_code
    out["flow page"] = c.put("/api/workspace/file", headers=_h(subject), json={
        "path": "flows/intake.md", "content": "# a flow\n", "slug": "_global"}).status_code
    out["company page"] = c.post("/api/workspace/entity", headers=_h(subject), json={
        "slug": "_global", "kind": "company", "name": "Acme", "facts": ["Acme sells widgets."],
        "source": "test"}).status_code
    out["reset"] = c.post("/api/workspace/reset", headers=_h(subject),
                          json={"target": "_global"}).status_code
    out["accept"] = c.post("/api/global/ready", headers=_h(subject), json={}).status_code
    out["target"] = c.post("/api/chat/target", headers=_h(subject),
                           json={"session": "c1", "workspace": "_global"}).status_code
    out["member op"] = c.post("/api/workspace/membership", headers=_h(subject), json={
        "slug": "_global", "email": "x@example.test", "role": "reader"}).status_code
    return out


# ── 2. EVERYDAY ACCOUNTS ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("subject", [FOUNDER, MEMBER], ids=["the instance admin", "a member"])
def test_an_everyday_account_writes_no_door_of_the_company_layer(stack, subject):
    c = _client(stack)
    refused = _company_writes(c, subject)
    assert set(refused.values()) == {403}, refused
    assert not (stack["root"] / "_global" / "POLICIES.md").exists()
    assert not (stack["root"] / "_global" / "flows").exists()


@pytest.mark.parametrize("subject", [FOUNDER, MEMBER], ids=["the instance admin", "a member"])
def test_an_everyday_account_mounts_the_company_layer_read_only(stack, subject):
    _client(stack)
    settings = load_settings(workspaces_dir=str(stack["root"]),
                             global_system_workspace_path=str(stack["root"] / "_global"))
    g = next(mt for mt in build_mount_set(settings, subject, []) if mt["role"] == "global")
    assert g["write"] is False


def test_nothing_configured_means_nobody_writes_the_company_layer(stack):
    c = _client(stack, admin_account="")
    for subject in (ADMIN_ACCOUNT, FOUNDER, MEMBER):
        assert set(_company_writes(c, subject).values()) == {403}, subject


# ── 3. THE ADMIN ACCOUNT ─────────────────────────────────────────────────────────────────────────

def test_the_admin_account_writes_the_company_layer(stack):
    c = _client(stack)
    assert c.put("/api/workspace/file", headers=_h(ADMIN_ACCOUNT), json={
        "path": "POLICIES.md", "content": "# Policies\n", "slug": "_global"}).status_code == 200
    assert c.post("/api/chat/target", headers=_h(ADMIN_ACCOUNT),
                  json={"session": "c1", "workspace": "_global"}).status_code == 200
    # …and the company layer's membership answer stays the explanatory one
    r = c.post("/api/workspace/membership", headers=_h(ADMIN_ACCOUNT), json={
        "slug": "_global", "email": "x@example.test", "role": "reader"})
    assert r.status_code == 409 and "POLICIES.md" in r.json()["detail"]


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/workspace/new", {"name": "Acme deal"}),
    ("POST", "/api/workspace/init", {}),
    ("POST", "/api/workspace/shared/new", {"name": "Acme deal"}),
    ("POST", "/api/workspace/invites/accept", {"token": "t"}),
    ("POST", "/api/workspace/activate", {"slug": "acme"}),
    ("GET", "/api/workspace/desk", None),
    ("GET", "/api/meeting/note", None),
    ("GET", "/api/meeting/stream", None),
])
def test_the_admin_account_opens_no_desk_no_workspace_and_no_meeting(stack, method, path, body):
    c = _client(stack)
    r = c.request(method, path, headers=_h(ADMIN_ACCOUNT), json=body)
    assert r.status_code == 403, (path, r.status_code, r.text)
    assert r.json()["detail"] == admin_account_scope.REFUSAL


@pytest.mark.parametrize("slug", ["", ADMIN_ACCOUNT, "acme"], ids=["own desk", "own desk by id", "a workspace"])
def test_the_workspace_door_resolves_nothing_but_the_company_layer_for_it(stack, slug):
    c = _client(stack)
    r = c.get("/api/workspace/tree", headers=_h(ADMIN_ACCOUNT), params={"slug": slug} if slug else {})
    assert r.status_code == 403 and r.json()["detail"] == admin_account_scope.REFUSAL
    r = c.put("/api/workspace/file", headers=_h(ADMIN_ACCOUNT),
              json={"path": "notes.md", "content": "x\n", "slug": slug})
    assert r.status_code == 403
    assert not (stack["root"] / ADMIN_ACCOUNT).exists(), "a desk appeared for the admin account"


def test_the_admin_account_aims_a_chat_nowhere_else(stack):
    c = _client(stack)
    r = c.post("/api/chat/target", headers=_h(ADMIN_ACCOUNT), json={"session": "c1", "workspace": "acme"})
    assert r.status_code == 403


def test_the_same_doors_stay_open_to_everyday_accounts(stack):
    """The refusal is the admin account's alone — an everyday account is not asked it."""
    c = _client(stack)
    r = c.get("/api/workspace/desk", headers=_h(MEMBER))
    assert r.status_code != 403 or r.json().get("detail") != admin_account_scope.REFUSAL


def test_the_admin_accounts_mount_table_is_the_company_layer_and_its_system_tier(stack):
    _client(stack)
    settings = load_settings(workspaces_dir=str(stack["root"]),
                             global_system_workspace_path=str(stack["root"] / "_global"))
    mounts = build_mount_set(settings, ADMIN_ACCOUNT, [{"workspace_id": "acme", "role": "owner"}],
                             room={"meeting_id": "1", "group_workspace_id": "acme", "ordered": []},
                             scaffold_workspaces=["acme"])
    assert [(mt["role"], mt["write"]) for mt in mounts] == [("global", True), ("system", True)]


def test_every_refused_door_is_a_route_this_app_serves(stack):
    """`assert_served` runs at boot; this pins that it would refuse a typo."""
    app = _client(stack).app
    admin_account_scope.assert_served(app.routes)
    with pytest.raises(admin_account_scope.ScopeError):
        admin_account_scope.assert_served(app.routes, frozenset({("POST", "/api/workspace/nope")}))


def test_the_admin_accounts_active_set_is_the_company_layer_and_says_so(stack):
    c = _client(stack)
    body = c.get("/api/workspace/active", headers=_h(ADMIN_ACCOUNT)).json()
    assert body["admin_account"] is True
    assert [w["slug"] for w in body["active"]] == ["_global"]
    everyday = c.get("/api/workspace/active", headers=_h(MEMBER)).json()
    assert "admin_account" not in everyday
    assert all(w["slug"] != "_global" or not w["write"] for w in everyday["active"])
