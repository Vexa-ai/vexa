"""Workspace STATES (Vexa-ai/vexa#1783, founder 2026-10-14): every workspace a person has is
ACTIVE (mounted by default), INACTIVE (listed, not mounted) or HIDDEN (not mounted, not listed,
reversible, history kept). Per person: hiding a shared workspace hides it for that member only.

One store: the person's attach state (``workspace_attach``), extended — never a second one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_plane import workspace_attach as attach
from control_plane import workspace_membership as membership
from control_plane import workspace_names


def _seed(root: Path, subject: str) -> Path:
    from shared.seeding import seed_workspace
    ws = root / subject
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("SEED")
    seed_workspace(ws, None)
    return ws


def _shared(root: Path, owner: str, name: str, *members: str) -> str:
    wid = attach.create_shared_workspace_dir(root, name)
    idx = membership.InMemoryMembershipIndex()
    membership.ensure_owner(root, wid, owner, index=idx, commit_fn=membership.policy_commit)
    for m in members:
        membership.grant_membership(root, wid, m, "contributor", added_by=owner, index=idx,
                                    commit_fn=membership.policy_commit)
    return wid


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "workspaces"
    _seed(r, "u1")
    _seed(r, "u2")
    return r


def _state(rows, slug):
    return next(r["state"] for r in rows if r["slug"] == slug)


def test_every_workspace_has_a_state_and_new_ones_start_active(root):
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    rows = attach.workspace_states(root, "u1", [])
    assert _state(rows, "personal") == "active"
    assert _state(rows, a) == "active"


def test_hide_unmounts_and_unhide_returns_to_active(root):
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    attach.set_workspace_state(root, "u1", a, "hidden")
    assert a not in {m.slug for m in attach.active_workspaces(root, "u1")}
    assert _state(attach.workspace_states(root, "u1", []), a) == "hidden"
    # the tree and its history are kept
    assert (attach.workspace_slot_dir(root, "u1", a) / ".git").is_dir()
    attach.set_workspace_state(root, "u1", a, "active")
    assert a in {m.slug for m in attach.active_workspaces(root, "u1")}
    assert _state(attach.workspace_states(root, "u1", []), a) == "active"


def test_inactive_is_listed_but_not_mounted(root):
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    attach.set_workspace_state(root, "u1", a, "inactive")
    assert a not in {m.slug for m in attach.active_workspaces(root, "u1")}
    assert _state(attach.workspace_states(root, "u1", []), a) == "inactive"


def test_personal_can_be_hidden_and_is_then_never_mounted(root):
    """Founder 2026-10-15: "I do not have Personal at all, I deleted it — why is it showing?" It
    cannot be deleted (it holds the desk), but it can be hidden like any workspace."""
    assert attach.set_workspace_state(root, "u1", "personal", "hidden") is True
    assert attach.active_workspaces(root, "u1") == []
    assert _state(attach.workspace_states(root, "u1", []), "personal") == "hidden"
    assert attach.set_workspace_state(root, "u1", "personal", "active") is True
    assert attach.SEED_SLOT in {m.slug for m in attach.active_workspaces(root, "u1")}


def test_unknown_workspace_is_refused(root):
    with pytest.raises(KeyError):
        attach.set_workspace_state(root, "u1", "nope", "hidden")


def test_archived_slot_reads_as_hidden(root):
    """The old Archive flag is the same state: one meaning, no migration step."""
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    attach.set_archived(root, "u1", a, True)
    assert _state(attach.workspace_states(root, "u1", []), a) == "hidden"
    attach.set_workspace_state(root, "u1", a, "active")
    assert _state(attach.workspace_states(root, "u1", []), a) == "active"
    assert attach._load_state(attach._store(root, "u1"))["slots"][a].get("archived") is False


def test_hiding_a_shared_workspace_is_per_member(root):
    wid = _shared(root, "u1", "Team", "u2")
    rows_u1 = membership.list_memberships(root, "u1")
    rows_u2 = membership.list_memberships(root, "u2")
    attach.set_workspace_state(root, "u2", wid, "hidden")
    # u2 no longer mounts it…
    assert wid not in {m.slug for m in attach.shared_active_mounts(root, "u2", rows_u2)}
    assert _state(attach.workspace_states(root, "u2", rows_u2), wid) == "hidden"
    # …u1 still does, and u2 is still a member
    assert wid in {m.slug for m in attach.shared_active_mounts(root, "u1", rows_u1)}
    assert _state(attach.workspace_states(root, "u1", rows_u1), wid) == "active"
    assert membership.is_member(root, wid, "u2") == "contributor"
    attach.set_workspace_state(root, "u2", wid, "active")
    assert wid in {m.slug for m in attach.shared_active_mounts(root, "u2", rows_u2)}


def test_shared_inactive_and_hidden_are_distinct(root):
    wid = _shared(root, "u1", "Team")
    rows = membership.list_memberships(root, "u1")
    attach.set_workspace_state(root, "u1", wid, "inactive")
    assert _state(attach.workspace_states(root, "u1", rows), wid) == "inactive"
    attach.set_workspace_state(root, "u1", wid, "hidden")
    assert _state(attach.workspace_states(root, "u1", rows), wid) == "hidden"
    # the legacy per-member switch never re-shows a hidden one
    attach.set_shared_active(root, "u1", wid, True)
    assert wid not in {m.slug for m in attach.shared_active_mounts(root, "u1", rows)}


def test_hide_all_and_unhide_all(root):
    slugs = [attach.create_workspace(root, "u1", name=f"W{i}").slug for i in range(3)]
    changed = attach.set_workspace_states(root, "u1", "all", "hidden", [])
    assert {c["slug"] for c in changed if c["changed"]} == set(slugs)   # personal is skipped, not refused
    assert {m.slug for m in attach.active_workspaces(root, "u1")} == {attach.SEED_SLOT}
    attach.set_workspace_states(root, "u1", "all", "active", [])
    assert set(slugs) <= {m.slug for m in attach.active_workspaces(root, "u1")}


def test_listing_carries_state_and_hidden_rows(root):
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    attach.set_workspace_state(root, "u1", a, "hidden")
    rows = workspace_names.listing(root, "u1")
    row = next(r for r in rows if r["slug"] == a)
    assert row["state"] == "hidden"
    assert next(r for r in rows if r["slug"] == "personal")["state"] == "active"


def test_hidden_state_survives_a_round_trip_through_disk(root):
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    attach.set_workspace_state(root, "u1", a, "hidden")
    raw = json.loads((attach._store(root, "u1") / attach.STATE_FILENAME).read_text())
    assert a in raw["hidden"]
