"""A SWITCHED-OFF PERSONAL WORKSPACE IS NEVER WRITTEN (#1783, founder 2026-10-15).

He deleted Personal; his active set became one workspace. During a Vexa call his chat — which names
"personal", as every new chat does — still mounted the Personal directory as the writable primary
and the turn's cwd, so the turn's desk README refresh committed into it ("Changed … 32 seconds ago")
and any meeting note the turn wrote would have landed there too. "personal" now means the person's
home (the first of their active set); the switched-off directory is mounted read-only.
"""
from __future__ import annotations

import json
from pathlib import Path

from control_plane.dispatch import build_mount_set
from shared.config import load_settings
from control_plane.workspace_attach import chat_read_mounts

SUBJECT = "2"


def _world(tmp_path: Path, *, personal_on: bool, hidden: bool | None = None) -> Path:
    hidden = (not personal_on) if hidden is None else hidden
    root = tmp_path / "workspaces"
    (root / SUBJECT).mkdir(parents=True)                         # the Personal directory (seed slot)
    (root / SUBJECT / "README.md").write_text("# This desk\n")
    store = root / ".attached" / SUBJECT
    (store / "biz-b1c87f").mkdir(parents=True)
    state = {"_flat_v1": True, "active": None,
             "active_set": (["seed"] if personal_on else []) + ["biz-b1c87f"],
             "hidden": ["seed"] if hidden else [], "slots": {"seed": {"repo": None, "ref": None},
                                      "biz-b1c87f": {"repo": None, "ref": "main", "name": "biz"}}}
    (store / "state.json").write_text(json.dumps(state))
    (root / "_global").mkdir()
    return root


def _settings(root: Path):
    return load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"))


def test_a_call_chat_naming_personal_never_mounts_a_switched_off_personal_for_writing(tmp_path):
    root = _world(tmp_path, personal_on=False)
    mounts = build_mount_set(_settings(root), SUBJECT, reads=["personal", "_global"])
    # a HIDDEN Personal is nothing: not mounted, and nothing stands in for it
    assert all(Path(m["path"]) != root / SUBJECT for m in mounts), "a hidden Personal was mounted"
    assert not any(m.get("desk") for m in mounts)


def test_an_inactive_but_not_hidden_personal_is_still_the_desk(tmp_path):
    root = _world(tmp_path, personal_on=False, hidden=False)
    mounts = chat_read_mounts(root, SUBJECT, ["personal", "biz-b1c87f"], None)
    desk = [m for m in mounts if getattr(m, "desk", False)]
    assert len(desk) == 1 and Path(desk[0].path) == root / SUBJECT and desk[0].write


def test_with_personal_on_nothing_changes(tmp_path):
    root = _world(tmp_path, personal_on=True)
    mounts = chat_read_mounts(root, SUBJECT, ["personal", "_global"], None)
    assert Path(mounts[0].path) == root / SUBJECT and mounts[0].write and mounts[0].primary


def test_a_chat_that_never_named_personal_gets_no_personal_mount(tmp_path):
    root = _world(tmp_path, personal_on=False)
    mounts = chat_read_mounts(root, SUBJECT, ["biz-b1c87f"], None)
    assert [Path(m.path).name for m in mounts] == ["biz-b1c87f"]


def test_the_worker_writes_notes_to_the_chats_target_and_refreshes_no_readme_by_order(tmp_path, monkeypatch):
    from worker.engine import _note_home, desk_mounts
    root = _world(tmp_path, personal_on=False)
    mounts = build_mount_set(_settings(root), SUBJECT, reads=["personal", "biz-b1c87f", "_global"])
    monkeypatch.setenv("VEXA_OWNER", SUBJECT)
    monkeypatch.delenv("VEXA_TARGET_WORKSPACE", raising=False)
    assert _note_home(mounts) is None                       # no target, Personal hidden: nowhere
    desk, _groups = desk_mounts(mounts)
    assert desk is None                                     # no README is refreshed in another workspace
    monkeypatch.setenv("VEXA_TARGET_WORKSPACE", "biz-b1c87f")
    assert _note_home(mounts).name == "biz-b1c87f"          # the chat's "Writes to" target