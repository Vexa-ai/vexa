"""Publishing or pushing a workspace from a model turn.

A chat other people read never publishes or pushes. A worker acting for its person (a delegated
identity) creates the repository private, under the token's own account, and may not name an owner
or a pre-created repository — the person does that from the UI. The workspace a call acts on is
held to the dispatch's ceiling. The person's own call keeps every option.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from control_plane import git_credentials
from control_plane.routers import workspaces as ws_router
from tests.test_workspace_manage_routes import _client, _seed_primary

ME = "u_jane"
PERSON = {"X-User-Id": ME}
WORKER = {**PERSON, "X-User-Regime": "human", "X-User-Delegation-Workspaces": "*"}
SHARED = {**WORKER, "X-User-Delegation-Shared": "1"}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    _seed_primary(tmp_path, ME)
    published: list = []
    pushed: list = []
    monkeypatch.setattr(git_credentials, "read_token", lambda root, subject, provider: "saved-token")
    monkeypatch.setattr(ws_router, "publish_workspace", lambda *a, **kw: published.append(kw) or SimpleNamespace(
        repo_url="https://example.test/r", pushed_ref="main", head_sha="0" * 40, created=True,
        provider=kw.get("provider")))
    monkeypatch.setattr(ws_router, "push_origin", lambda *a, **kw: pushed.append(kw) or SimpleNamespace(
        remote="origin", url="https://example.test/r", branch="main", head_sha="0" * 40))
    return _client(tmp_path), published, pushed


def _publish(c, headers, **body):
    return c.post("/api/workspace/publish", headers=headers, json={"repo_name": "kg", **body})


def test_a_shared_turn_never_publishes(world):
    c, published, _ = world
    r = _publish(c, SHARED)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "shared_conversation"
    assert not published


def test_a_shared_turn_never_pushes(world):
    c, _, pushed = world
    r = c.post("/api/workspace/push", headers=SHARED, json={})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "shared_conversation"
    assert not pushed


def test_a_worker_publishes_private_whatever_it_asks(world):
    c, published, _ = world
    r = _publish(c, WORKER, private=False)
    assert r.status_code == 200, r.text
    assert published and published[-1]["private"] is True


@pytest.mark.parametrize("extra", [{"org": "acme"}, {"remote_url": "https://example.test/x.git"}])
def test_a_worker_may_not_name_the_owner_or_the_repository(world, extra):
    c, published, _ = world
    r = _publish(c, WORKER, **extra)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "publish_from_the_ui"
    assert not published


def test_a_workspace_outside_the_ceiling_is_refused(world):
    c, published, pushed = world
    worker = {**WORKER, "X-User-Delegation-Workspaces": "ws_1"}
    r = _publish(c, worker, slug="ws_other")
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "out_of_scope"
    r = c.post("/api/workspace/push", headers=worker, json={"slug": "ws_other"})
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "out_of_scope"
    assert not published and not pushed


def test_the_persons_own_call_keeps_every_option(world):
    c, published, _ = world
    r = _publish(c, PERSON, private=False, org="acme")
    assert r.status_code == 200, r.text
    assert published[-1]["private"] is False and published[-1]["org"] == "acme"
