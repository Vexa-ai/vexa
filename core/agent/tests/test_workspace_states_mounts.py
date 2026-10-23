"""What a turn MOUNTS follows the workspace states (Vexa-ai/vexa#1783, founder 2026-10-14).

* A HIDDEN workspace is never on the worker's disk: not through the person's active set, not
  through a chat's reads, not through a scaffold naming it. The worker cannot read what it was
  never given (the runtime binds exactly this list).
* A chat with its OWN reads mounts exactly those (plus where it writes); a chat without follows
  the person's default active set — which is what every chat did before.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from control_plane import workspace_attach as attach
from control_plane.dispatch import build_mount_set
from control_plane.workspace_membership import (InMemoryMembershipIndex, ensure_owner,
                                                grant_membership, policy_commit)
from shared.config import load_settings


def _settings(root: Path):
    return load_settings(workspaces_dir=str(root),
                         global_system_workspace_path=str(root / "_global"),
                         internal_api_secret="s", ui_url="https://app.example.test", redis_url="")


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    from shared.seeding import seed_workspace
    for s in ("175", "176"):
        (root / s).mkdir(parents=True)
        (root / s / "CLAUDE.md").write_text("SEED")
        seed_workspace(root / s, None)
    return root


def _shared(store, name, owner="175", member=None):
    wid = attach.create_shared_workspace_dir(store, name)
    idx = InMemoryMembershipIndex()
    ensure_owner(store, wid, owner, index=idx, commit_fn=policy_commit)
    if member:
        grant_membership(store, wid, member, "contributor", added_by=owner, index=idx, commit_fn=policy_commit)
    return wid


def _middle(mounts):
    return [m["slug"] for m in mounts if m.get("role") not in ("global", "system")]


def test_a_hidden_own_workspace_is_not_mounted(store):
    a = attach.create_workspace(store, "175", name="Alpha").slug
    b = attach.create_workspace(store, "175", name="Beta").slug
    attach.set_workspace_state(store, "175", a, "hidden")
    mid = _middle(build_mount_set(_settings(store), "175", []))
    assert a not in mid and b in mid


def test_a_hidden_shared_workspace_is_not_mounted_for_that_member_only(store):
    wid = _shared(store, "Team", member="176")
    rows = [{"workspace_id": wid, "role": "contributor"}]
    attach.set_workspace_state(store, "176", wid, "hidden")
    assert wid not in _middle(build_mount_set(_settings(store), "176", rows))
    assert wid in _middle(build_mount_set(_settings(store), "175", [{"workspace_id": wid, "role": "owner"}]))


def test_a_scaffold_cannot_mount_a_hidden_workspace(store):
    wid = _shared(store, "Team")
    rows = [{"workspace_id": wid, "role": "owner"}]
    attach.set_workspace_state(store, "175", wid, "hidden")
    mounts = build_mount_set(_settings(store), "175", rows, scaffold_workspaces=[wid])
    assert wid not in _middle(mounts)
    assert not any(str(m.get("path", "")).endswith(f"/{wid}") for m in mounts)


def test_chat_reads_mount_exactly_those_plus_the_target(store):
    a = attach.create_workspace(store, "175", name="Alpha").slug
    b = attach.create_workspace(store, "175", name="Beta").slug
    wid = _shared(store, "Team")
    rows = [{"workspace_id": wid, "role": "owner"}]
    mid = _middle(build_mount_set(_settings(store), "175", rows, reads=["personal", a], target=wid))
    assert set(mid) == {attach.SEED_SLOT, a, wid}
    assert b not in mid


def test_chat_reads_can_mount_an_inactive_one_but_never_a_hidden_one(store):
    a = attach.create_workspace(store, "175", name="Alpha").slug
    b = attach.create_workspace(store, "175", name="Beta").slug
    attach.set_workspace_state(store, "175", a, "inactive")
    attach.set_workspace_state(store, "175", b, "hidden")
    mid = _middle(build_mount_set(_settings(store), "175", [], reads=["personal", a, b]))
    assert a in mid and b not in mid


def test_chat_reads_without_personal_drop_the_desk_when_it_writes_elsewhere(store):
    a = attach.create_workspace(store, "175", name="Alpha").slug
    mid = _middle(build_mount_set(_settings(store), "175", [], reads=[a], target=a))
    assert mid == [a]


def test_no_reads_follows_the_default_active_set(store):
    a = attach.create_workspace(store, "175", name="Alpha").slug
    b = attach.create_workspace(store, "175", name="Beta").slug
    attach.set_workspace_state(store, "175", b, "inactive")
    mid = _middle(build_mount_set(_settings(store), "175", []))
    assert attach.SEED_SLOT in mid and a in mid and b not in mid


def test_reads_never_widen_to_a_workspace_the_person_is_not_a_member_of(store):
    wid = _shared(store, "Theirs", owner="176")
    mid = _middle(build_mount_set(_settings(store), "175", [], reads=["personal", wid]))
    assert wid not in mid


def test_a_chat_with_reads_and_no_target_still_mounts_the_desk_it_writes_to(store):
    a = attach.create_workspace(store, "175", name="Alpha").slug
    mid = _middle(build_mount_set(_settings(store), "175", [], reads=[a], target=""))
    assert attach.SEED_SLOT in mid and a in mid


def test_a_chat_with_no_workspaces_mounts_no_workspace(store):
    attach.create_workspace(store, "175", name="Alpha")
    mid = _middle(build_mount_set(_settings(store), "175", [], reads=[], target="", no_target=True))
    assert mid == []
