"""A worker dispatched without a person is held to its workspace ceiling on every verb that names one.

The ceiling (`x-user-delegation-workspaces`, on the signed identity) was checked on reads and page
writes (`_read_target`) and nowhere else, so a worker granted workspace A could still invite into,
re-role, unshare, delete or reset any other workspace its person owns, and write the company layer.
Every route that names a workspace in its path or body now asks `api_shared.require_in_ceiling`
before it acts. The account's own rules (owner, contributor, admin) still apply after it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import identity_token
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from workspaces.shared.workspace_id import mint_id

KEY = identity_token.generate_signing_key()


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


@pytest.fixture
def client(monkeypatch, tmp_path):
    public = tmp_path / "identity-public-key.pem"
    public.write_bytes(identity_token.public_key_pem(KEY))
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("INTERNAL_API_SECRET", "agent-test-internal-secret")
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setenv("VEXA_WORKSPACES_DIR", str(root))
    # A route the ceiling lets through may still fail further in on this bare store; that is the
    # route answering, which is all the "not refused by the ceiling" cases need to see.
    return TestClient(create_app(Dispatcher(load_settings(), _Runtime(), _Identity()),
                                 reader=WorkspaceReader(str(root))),
                      raise_server_exceptions=False)


def _as(workspaces, regime="autonomous") -> dict:
    claims = {"sub": "7", "email": "ada@example.com",
              "delegation": {"regime": regime, "workspaces": workspaces}}
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


OUTSIDE = "ws_b"
VERBS = [
    ("DELETE", f"/api/workspace/{OUTSIDE}", None, None),
    ("POST", f"/api/workspace/{OUTSIDE}/archive", None, {"archived": True}),
    ("POST", f"/api/workspace/{OUTSIDE}/unshare", None, None),
    ("POST", f"/api/workspace/{OUTSIDE}/share-enable", None, None),
    ("POST", f"/api/workspace/{OUTSIDE}/deploy-key", None, {}),
    ("POST", f"/api/workspace/{OUTSIDE}/leave", None, None),
    ("POST", "/api/workspace/invites", None, {"workspace_id": OUTSIDE}),
    ("DELETE", "/api/workspace/invites/inv1", {"workspace_id": OUTSIDE}, None),
    ("DELETE", "/api/workspace/members/u9", {"workspace_id": OUTSIDE}, None),
    ("POST", "/api/workspace/members/u9/role", {"workspace_id": OUTSIDE}, {"role": "viewer"}),
    ("POST", "/api/workspace/invite", None,
     {"slug": OUTSIDE, "email": "eve@example.com", "role": "contributor"}),
    ("POST", "/api/workspace/membership", None,
     {"slug": OUTSIDE, "email": "eve@example.com", "role": "remove"}),
    ("POST", "/api/workspace/reset", None, {"target": "_global"}),
    ("PUT", "/api/workspace/file", None, {"path": "README.md", "content": "x", "slug": "_global"}),
    ("POST", "/api/global/ready", None, {}),
    ("POST", f"/api/workspaces/{OUTSIDE}/rename", None, {"name": "Elsewhere"}),
    ("POST", f"/api/workspace/shared/{OUTSIDE}/attach", None, {}),
    ("POST", f"/api/workspace/shared/{OUTSIDE}/active", None, {"active": False}),
]


def _call(client, method, path, params, body, headers):
    return client.request(method, path, params=params, json=body, headers=headers)


def _out_of_scope(r) -> bool:
    try:
        detail = r.json().get("detail")
    except ValueError:
        return False
    return r.status_code == 403 and isinstance(detail, dict) and detail.get("refused") == "out_of_scope"


@pytest.mark.parametrize("method,path,params,body", VERBS)
def test_a_verb_outside_the_ceiling_is_refused(client, method, path, params, body):
    r = _call(client, method, path, params, body, _as(["ws_a"]))
    assert _out_of_scope(r), (method, path, r.status_code, r.text)


@pytest.mark.parametrize("method,path,params,body", VERBS)
def test_an_unbounded_grant_is_held_only_by_the_account(client, method, path, params, body):
    r = _call(client, method, path, params, body, _as("*", regime="human"))
    assert not _out_of_scope(r), (method, path, r.text)


@pytest.mark.parametrize("method,path,params,body", VERBS[:12])
def test_the_workspace_inside_the_ceiling_is_not_refused_by_it(client, method, path, params, body):
    r = _call(client, method, path, params, body, _as([OUTSIDE]))
    assert not _out_of_scope(r), (method, path, r.text)


def test_an_empty_ceiling_refuses_every_named_workspace(client):
    r = client.delete(f"/api/workspace/{OUTSIDE}", headers=_as([]))
    assert _out_of_scope(r)


def test_rename_by_id_is_held_to_the_ceiling_by_the_slug_the_id_names(client):
    """The ceiling lists slugs; rename addresses a workspace by its stable id. A worker granted the
    workspace whose slug the id resolves to is not refused by the ceiling, and one granted something
    else is."""
    wid = mint_id()
    client.app.state.workspace_registry.put({"id": wid, "slug": OUTSIDE, "kind": "group", "name": "B"})
    assert _out_of_scope(client.post(f"/api/workspaces/{wid}/rename", json={"name": "x"},
                                      headers=_as(["ws_a"])))
    assert not _out_of_scope(client.post(f"/api/workspaces/{wid}/rename", json={"name": "x"},
                                          headers=_as([OUTSIDE])))
