"""Access-gate deny vectors for workspace links and invites (Vexa-ai/vexa#1783).

Pure unit tests, no network:

* a delegated dispatch whose ceiling excludes a workspace resolves a link into it with no
  workspace name, the same answer an outside reader gets everywhere else;
* an invite token that is revoked, expired or used up previews as ``{valid: false, reason}`` and
  nothing else: no workspace, purpose, inviter or bound addresses.
"""
from __future__ import annotations

import pytest

from control_plane import link_resolver, workspace_ids as ids
from control_plane import workspace_membership as m
from workspaces.shared.entities import upsert_entity

from .test_invite_link import _client, _h, _init_ws


# ── link resolution outside a delegated ceiling ─────────────────────────────────────────────────

@pytest.fixture()
def linked(tmp_path):
    """Group `grp` (126 is a member, 127 is not) plus 127's own desk."""
    (tmp_path / "127" / "kg" / "entities").mkdir(parents=True)
    (tmp_path / "grp" / "policy").mkdir(parents=True)
    (tmp_path / "grp" / "policy" / "members.json").write_text('[{"subject":"126","role":"owner"}]')
    upsert_entity(tmp_path / "grp", "person", "Robin Vale", ["Chairs the TSC."], "the meeting")
    upsert_entity(tmp_path / "127", "person", "Nora Quill", ["Attends."], "the meeting")
    reg = ids.WorkspaceRegistry()
    ids.migrate(tmp_path, reg)
    member = lambda root, slug, subject: "owner" if (slug, subject) == ("grp", "126") else None  # noqa: E731
    return tmp_path, reg, member


def test_outside_the_ceiling_a_non_member_learns_no_workspace_name(linked):
    root, reg, member = linked
    gid = reg.by_slug("grp")["id"]
    r = link_resolver.resolve(f"ws:{gid}/robin-vale", subject="127", root=root, registry=reg,
                              is_member=member, within=lambda slug: slug == "127")
    assert r["access"] == ids.ACCESS_NOT_YOURS
    assert r["workspace"] is None
    assert r["url"] is None and r["writable"] is False


def test_outside_the_ceiling_a_member_is_answered_not_yours_and_keeps_the_name(linked):
    """The person is a member; only the dispatch is narrowed. The name is theirs to see."""
    root, reg, member = linked
    gid = reg.by_slug("grp")["id"]
    r = link_resolver.resolve(f"ws:{gid}/robin-vale", subject="126", root=root, registry=reg,
                              is_member=member, within=lambda slug: False)
    assert r["access"] == ids.ACCESS_NOT_YOURS
    assert r["url"] is None and r["writable"] is False
    assert r["workspace"] == "grp"


def test_resolve_many_outside_the_ceiling_names_nothing_for_a_non_member(linked):
    root, reg, member = linked
    gid = reg.by_slug("grp")["id"]
    out = link_resolver.resolve_many([f"ws:{gid}/robin-vale"], subject="127", root=root,
                                     registry=reg, is_member=member, within=lambda slug: False)
    assert [r["workspace"] for r in out] == [None]


# ── invite preview once the token is no longer usable ───────────────────────────────────────────

@pytest.fixture()
def shared(tmp_path):
    _init_ws(tmp_path, "pilot-a1b2c3")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "pilot-a1b2c3", "owner1", index=idx, email="jane@example.com")
    c = _client(tmp_path, idx)
    minted = c.post("/api/workspace/invites", headers=_h("owner1"),
                    json={"workspace_id": "pilot-a1b2c3", "role": "contributor", "max_uses": 1,
                          "allowed_emails": ["jsmith@example.com"]}).json()
    return tmp_path, c, minted


def _preview(c, token):
    r = c.get("/api/workspace/invites/preview", params={"token": token})
    assert r.status_code == 200
    return r.json()


def test_a_revoked_invite_previews_as_valid_false_and_reason_only(shared):
    root, c, minted = shared
    assert c.delete(f"/api/workspace/invites/{minted['id']}", headers=_h("owner1"),
                    params={"workspace_id": "pilot-a1b2c3"}).status_code in (200, 204)
    assert _preview(c, minted["token"]) == {"valid": False, "reason": "revoked"}


def test_a_used_up_invite_previews_as_valid_false_and_reason_only(shared):
    root, c, minted = shared
    assert c.post("/api/workspace/invites/accept",
                  headers={**_h("u_first"), "x-user-email": "jsmith@example.com"},
                  json={"token": minted["token"]}).status_code == 200
    assert _preview(c, minted["token"]) == {"valid": False, "reason": "used_up"}


def test_an_expired_invite_previews_as_valid_false_and_reason_only(shared):
    root, c, minted = shared
    assert m.preview_invite(root, minted["token"], now=9_999_999_999) == {
        "valid": False, "reason": "expired"}


def test_a_valid_invite_still_previews_in_full(shared):
    """Signed in, the confirm card (D26) shows the sender and the bound address. Nobody signed in, the
    link's holder learns the workspace's name and the terms only (security review M1, 0.13.4 S4)."""
    root, c, minted = shared
    anon = _preview(c, minted["token"])
    assert anon["valid"] is True and anon["workspace_id"] == "pilot-a1b2c3"
    assert not {"shared_by", "purpose"} & set(anon) and anon["restricted_to"] == ["jsmith@example.com"]
    r = c.get("/api/workspace/invites/preview", params={"token": minted["token"]}, headers=_h("u_first"))
    p = r.json()
    assert p["shared_by"] == "jane@example.com" and p["restricted_to"] == ["jsmith@example.com"]
