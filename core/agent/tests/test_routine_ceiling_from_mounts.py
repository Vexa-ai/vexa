"""A ROUTINE'S WRITE CEILING IS ITS WRITABLE MOUNTS (Vexa-ai/vexa#1783, routine-session-write-scope).

A routine turn runs unwatched, so its delegation token carries a hard ceiling. That ceiling used to be
the dispatch's `workspaces` list — which every caller leaves at its default, the person's own desk —
while the worker's filesystem came from the mount set (the chat's reads, its target, the person's
shared workspaces). Two sources for one fact, and they disagreed: a routine with a shared workspace
mounted read-write was refused `out_of_scope` the moment it called `entity_upsert` there.

One source now: the ceiling is the workspaces mounted WRITABLE for this turn. Never wider — a
read-only mount and an unmounted workspace stay outside it.
"""
from __future__ import annotations

import pytest

from control_plane.dispatch import build_unit_env
from control_plane.workspace_attach import create_shared_workspace_dir
from control_plane.workspace_membership import (InMemoryMembershipIndex, ensure_owner,
                                                grant_membership)
from shared import delegation, units
from shared.config import load_settings

SECRET = "a-secret-for-tests-0123456789abcdef"


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    (root / "175").mkdir(parents=True)
    return root


def _settings(root):
    s = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                      internal_api_secret="s", ui_url="https://app.example.test", redis_url="",
                      mcp_url="https://mcp.example.test/mcp")
    return s.model_copy(update={"mcp_delegation_secret": s.mcp_delegation_secret.__class__(SECRET)})


def _scope(settings, *, trigger="scheduled", **kw) -> dict:
    inv = units.make_dispatch(subject="175", trigger=trigger, start=units.entrypoint(inline="run"),
                              context={"kind": "none", "session": "pchat-abc"})
    env = build_unit_env(settings, inv, unit_id="u1", token="t", **kw)
    return delegation.verify_delegation(SECRET, env["VEXA_MCP_DELEGATION_TOKEN"])["scope"]


def _workspaces(store):
    """One the person owns (mounted rw), one they only view (mounted ro), one they are not in."""
    idx = InMemoryMembershipIndex()
    owned = create_shared_workspace_dir(store, "Owned Project")
    ensure_owner(store, owned, "175", index=idx)
    viewed = create_shared_workspace_dir(store, "Viewed Project")
    ensure_owner(store, viewed, "900", index=idx)
    grant_membership(store, viewed, "175", "viewer", added_by="900", index=idx)
    stranger = create_shared_workspace_dir(store, "Somebody Elses")
    ensure_owner(store, stranger, "901", index=idx)
    members = [{"workspace_id": owned, "role": "owner"}, {"workspace_id": viewed, "role": "viewer"}]
    return owned, viewed, stranger, members


def _allows(scope, slug) -> bool:
    return delegation.ceiling_allows(scope["workspaces"], slug, subject="175")


def test_a_routine_may_write_a_workspace_mounted_read_write_for_it(store):
    owned, _viewed, _stranger, members = _workspaces(store)
    scope = _scope(_settings(store), memberships=members, target=owned)
    assert scope["regime"] == "autonomous"
    assert _allows(scope, owned), scope


def test_a_read_only_or_unmounted_workspace_stays_outside_the_ceiling(store):
    owned, viewed, stranger, members = _workspaces(store)
    scope = _scope(_settings(store), memberships=members, target=owned)
    assert not _allows(scope, viewed)
    assert not _allows(scope, stranger)
    assert not _allows(scope, "_global")          # mounted, but read-only for everybody but admin
    assert _allows(scope, "175")                  # the person's own desk, as always


def test_a_chat_with_its_own_reads_only_writes_where_it_works(store):
    """A chat that names its reads mounts its target rw; a workspace it only reads is not a grant."""
    owned, viewed, _stranger, members = _workspaces(store)
    scope = _scope(_settings(store), memberships=members, target=owned, reads=[viewed])
    assert _allows(scope, owned)
    assert not _allows(scope, viewed)


def test_the_ceiling_never_names_a_system_tier(store):
    _owned, _viewed, _stranger, members = _workspaces(store)
    scope = _scope(_settings(store), memberships=members)
    assert "_system" not in scope["workspaces"] and "_global" not in scope["workspaces"]


def test_an_event_agent_and_a_watched_turn_are_unchanged(store):
    owned, _viewed, _stranger, members = _workspaces(store)
    assert _scope(_settings(store), memberships=members, target=owned,
                  least_privilege=True)["workspaces"] == []
    assert _scope(_settings(store), trigger="message", memberships=members)["workspaces"] == "*"


# ── reads are not writes: a read-only mount is readable through the tools (Vexa-ai/vexa#1783) ──

def _reads(scope, slug) -> bool:
    return delegation.ceiling_reads(scope["workspaces"], slug, subject="175")


def test_a_routine_may_read_a_workspace_mounted_read_only_but_not_write_it(store):
    owned, viewed, stranger, members = _workspaces(store)
    scope = _scope(_settings(store), memberships=members, target=owned)
    assert _reads(scope, viewed) and not _allows(scope, viewed)
    assert _reads(scope, owned) and _allows(scope, owned)
    assert not _reads(scope, stranger)
    assert "_system" not in json_names(scope) and "ro:_system" not in json_names(scope)


def test_a_read_only_entry_never_matches_a_write():
    ceiling = ["alpha", "ro:beta"]
    assert delegation.ceiling_reads(ceiling, "beta") and not delegation.ceiling_allows(ceiling, "beta")
    assert not delegation.ceiling_allows(ceiling, "ro:beta") and not delegation.ceiling_reads(ceiling, "ro:beta")
    assert not delegation.scope_allows_workspace({"scope": {"workspaces": ceiling}}, "beta")


def json_names(scope) -> list:
    return list(scope["workspaces"])
