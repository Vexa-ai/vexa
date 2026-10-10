"""A workspace's member list is never read or written through a link.

``policy/members.json`` is the authoritative member list: agent-api reads it to decide who may mount a
workspace (``is_member``, the active set, the history sweep) and rewrites it on every grant. The work
tree it sits in is the model's tools' to write during a turn, so a link planted at
``policy/members.json`` — or at ``policy`` — could make one workspace's list another's: read
through, a workspace would answer with somebody else's members; written through, a grant on the
attacker's workspace would land in the victim's list. Neither may happen: the list is read without
following a link (a regular file with a single link only, else no members), and written as a new
file renamed over the name inside a folder reached without following a link.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from control_plane import workspace_membership as m

VICTIM = [{"subject": "u_victim", "role": "owner", "email": "victim@example.test"}]


def _world(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "store"
    victim = root / "wsVictim"
    (victim / "policy").mkdir(parents=True)
    (victim / "policy" / "members.json").write_text(json.dumps(VICTIM))
    mine = root / "wsMine"
    mine.mkdir()
    return root, victim, mine


def _plant(mine: Path, victim: Path, where: str) -> None:
    if where == "file":
        (mine / "policy").mkdir()
        (mine / "policy" / "members.json").symlink_to(victim / "policy" / "members.json")
    else:
        (mine / "policy").symlink_to(victim / "policy", target_is_directory=True)


@pytest.mark.parametrize("where", ["file", "folder"])
def test_a_member_list_reached_through_a_link_is_no_member_list(tmp_path, where):
    root, victim, mine = _world(tmp_path)
    _plant(mine, victim, where)
    assert m.read_members(root, "wsMine") == []
    assert m.is_member(root, "wsMine", "u_victim") is None
    assert all(r["workspace_id"] != "wsMine" for r in m.list_memberships(root, "u_victim"))
    assert m.read_members(root, "wsVictim") == VICTIM            # the real list still reads


@pytest.mark.parametrize("where", ["file", "folder"])
def test_a_grant_is_never_written_through_a_link(tmp_path, where):
    root, victim, mine = _world(tmp_path)
    _plant(mine, victim, where)
    before = (victim / "policy" / "members.json").read_text()
    try:
        m.ensure_owner(root, "wsMine", "u_attacker", index=m.InMemoryMembershipIndex())
    except m.MembershipError:
        assert where == "folder"                                  # a linked folder refuses the grant
    assert (victim / "policy" / "members.json").read_text() == before
    assert m.is_member(root, "wsVictim", "u_attacker") is None
    if where == "file":                                           # the link was replaced, not followed
        own = mine / "policy" / "members.json"
        assert not own.is_symlink()
        assert [r["subject"] for r in json.loads(own.read_text())] == ["u_attacker"]


def test_a_member_list_with_a_second_hard_link_is_no_member_list(tmp_path):
    root, victim, mine = _world(tmp_path)
    (mine / "policy").mkdir()
    os.link(victim / "policy" / "members.json", mine / "policy" / "members.json")
    assert m.read_members(root, "wsMine") == []


def test_an_ordinary_member_list_reads_and_writes(tmp_path):
    root, _victim, _mine = _world(tmp_path)
    m.ensure_owner(root, "wsMine", "u1", index=m.InMemoryMembershipIndex())
    assert m.is_member(root, "wsMine", "u1") == "owner"
    m.ensure_owner(root, "wsMine", "u1", email="u1@example.test", index=m.InMemoryMembershipIndex())
    assert m.read_members(root, "wsMine")[0]["email"] == "u1@example.test"


def test_a_first_grant_still_makes_the_policy_folder(tmp_path):
    root = tmp_path / "store"
    m.ensure_owner(root, "wsNew", "u1", index=m.InMemoryMembershipIndex())
    assert m.is_member(root, "wsNew", "u1") == "owner"
