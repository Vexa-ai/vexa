"""A signed-in caller who is NOT a member of a group workspace.

Three answers, each from the authoritative roster: switching the group ON in the caller's active
set is refused; the identity routes answer `not-yours` without the group's name or anything the
caller did not supply; a link into the group carries no workspace name. A member, and the admin
account, still see the name. Switching OFF stays open to anyone — it only edits the caller's own
set, and is how a client clears a workspace the caller was removed from.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from control_plane import global_layer
from control_plane import workspace_ids as ids
from control_plane import workspace_membership as m
from control_plane.workspace_attach import attached_workspaces

from tests.test_workspace_id_api import _client, _h, _init_ws


def _world(root: Path):
    _init_ws(root, "36")
    _init_ws(root, "39")
    _init_ws(root, "team-abc123")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(root, "team-abc123", "36", index=idx)
    c = _client(root, idx)
    gid = c.get("/api/workspaces/by-slug/team-abc123", headers=_h("36")).json()["id"]
    c.post(f"/api/workspaces/{gid}/rename", headers=_h("36"), json={"name": "Team Secret Name"})
    return c, gid


def _hidden(root: Path, subject: str) -> list:
    return attached_workspaces(root, subject).get("hidden_shared", [])


def test_a_non_member_cannot_switch_a_group_on(tmp_path):
    c, _ = _world(tmp_path)
    r = c.post("/api/workspace/shared/team-abc123/active", json={"active": True}, headers=_h("39"))
    assert r.status_code == 403, r.text


def test_a_member_can_switch_it_on_and_off(tmp_path):
    c, _ = _world(tmp_path)
    off = c.post("/api/workspace/shared/team-abc123/active", json={"active": False}, headers=_h("36"))
    assert off.status_code == 200 and "team-abc123" in _hidden(tmp_path, "36")
    on = c.post("/api/workspace/shared/team-abc123/active", json={"active": True}, headers=_h("36"))
    assert on.status_code == 200 and "team-abc123" not in _hidden(tmp_path, "36")


def test_switching_off_stays_allowed_for_a_non_member(tmp_path):
    """Cleanup after removal: it writes only the caller's own hidden list."""
    c, _ = _world(tmp_path)
    r = c.post("/api/workspace/shared/team-abc123/active", json={"active": False}, headers=_h("39"))
    assert r.status_code == 200


@pytest.mark.parametrize("by", ["slug", "id"])
def test_not_yours_carries_no_name_and_nothing_beyond_what_was_asked(tmp_path, by):
    c, gid = _world(tmp_path)
    url = "/api/workspaces/by-slug/team-abc123" if by == "slug" else f"/api/workspaces/{gid}"
    r = c.get(url, headers=_h("39"))
    assert r.status_code == 200
    body = r.json()
    assert body["access"] == ids.ACCESS_NOT_YOURS and body["writable"] is False
    assert body["name"] is None and body["kind"] is None
    assert "Team Secret Name" not in r.text
    if by == "slug":
        assert body["id"] is None and body["slug"] == "team-abc123"    # the id was not supplied
    else:
        assert body["id"] == gid and "team-abc123" not in r.text       # the slug was not supplied


@pytest.mark.parametrize("by", ["slug", "id"])
def test_a_member_still_gets_the_name(tmp_path, by):
    c, gid = _world(tmp_path)
    url = "/api/workspaces/by-slug/team-abc123" if by == "slug" else f"/api/workspaces/{gid}"
    body = c.get(url, headers=_h("36")).json()
    assert body["access"] == ids.ACCESS_READABLE and body["name"] == "Team Secret Name"


def test_the_admin_account_still_gets_the_name(tmp_path, monkeypatch):
    c, gid = _world(tmp_path)
    monkeypatch.setattr(global_layer, "is_admin_account", lambda settings, s: s == "1")
    body = c.get(f"/api/workspaces/{gid}", headers=_h("1")).json()
    assert body["access"] == ids.ACCESS_NOT_YOURS and body["name"] == "Team Secret Name"


def test_a_link_into_the_group_names_no_workspace_for_a_non_member(tmp_path):
    c, gid = _world(tmp_path)
    out = c.post("/api/links/resolve", headers=_h("39"),
                 json={"refs": [f"ws:{gid}/someone"]}).json()["results"]
    assert out[0]["access"] == ids.ACCESS_NOT_YOURS and out[0]["workspace"] is None
