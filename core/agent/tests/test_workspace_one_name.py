"""ONE NAME PER WORKSPACE ID (Vexa-ai/vexa#1783, founder 2026-10-15: "badges disagree personal = biz —
it's misleading"). User 2 on app.dev: the desk (`<root>/2`) is occupied by a legacy swapped-in slot
(`biz-864aed18`, nested), the only active member is a separate biz clone (`biz-b1c87fc32ecb`, named
"biz"), and the original personal sits parked in `seed`. The listing used to name the PERSONAL row
after whatever was first in the active set, so Personal read "biz" with the clone's id and the clone
had no row of its own. Personal is the desk and says "Personal"; the clone is its own row "biz"; the
legacy pointer is never a workspace name."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_plane import workspace_attach as attach
from control_plane import workspace_names


def _git_dir(p: Path, marker: str) -> None:
    p.mkdir(parents=True, exist_ok=True)
    (p / "README.md").write_text(marker)


@pytest.fixture
def user2(tmp_path):
    root = tmp_path / "workspaces"
    _git_dir(root / "2", "desk")
    store = root / attach.STORE_DIRNAME / "2"
    _git_dir(store / "biz-b1c87fc32ecb", "biz clone")
    _git_dir(store / "seed", "original personal")
    (store / attach.STATE_FILENAME).write_text(json.dumps({
        "_flat_v1": True,
        "active": "biz-864aed18",
        "active_set": ["biz-b1c87fc32ecb"],
        "hidden": [], "hidden_shared": [],
        "slots": {
            "biz-864aed18": {"nested": True, "ref": "main", "repo": "git@github.com:DmitriyG228/biz.git"},
            "biz-b1c87fc32ecb": {"archived": False, "name": "biz", "nested": False, "ref": "main",
                                 "repo": "git@github.com:DmitriyG228/biz.git"},
            "seed": {"ref": None, "repo": None},
        },
    }))
    return root


def test_personal_is_the_desk_and_says_personal(user2):
    rows = workspace_names.listing(user2, "2")
    personal = next(r for r in rows if r["slug"] == "personal")
    assert personal["name"] == "Personal" and personal["display"] == "Personal"
    assert personal.get("also") != "biz-b1c87fc32ecb"


def test_the_repo_that_is_mounted_first_has_its_own_row_by_its_own_name(user2):
    rows = workspace_names.listing(user2, "2")
    biz = [r for r in rows if r["name"] == "biz"]
    assert [r["slug"] for r in biz] == ["biz-b1c87fc32ecb"]
    assert biz[0]["state"] == "active" and "same_name_as" not in biz[0]


def test_the_legacy_desk_pointer_is_never_a_workspace_name(user2):
    rows = workspace_names.listing(user2, "2")
    assert all(r["slug"] != "biz-864aed18" and r["name"] != "biz-864aed18" for r in rows)
    assert all(r["slug"] != "seed" for r in rows)


def test_one_name_per_id_and_names_resolve_to_their_own_row(user2):
    rows = workspace_names.listing(user2, "2")
    names = {}
    for r in rows:
        names.setdefault(r["slug"], set()).add(r["name"])
    assert all(len(v) == 1 for v in names.values())
    assert workspace_names.resolve(rows, "biz", "2") == "biz-b1c87fc32ecb"
    assert workspace_names.resolve(rows, "Personal", "2") in ("Personal", "personal")


def test_the_mount_says_which_one_is_the_desk(user2):
    mounts = attach.active_workspaces(user2, "2")
    assert [(m.slug, m.primary, m.desk) for m in mounts] == [("biz-b1c87fc32ecb", True, False)]


def test_an_ordinary_person_is_unchanged(tmp_path):
    root = tmp_path / "workspaces"
    _git_dir(root / "7", "desk")
    rows = workspace_names.listing(root, "7")
    assert [(r["slug"], r["name"]) for r in rows] == [("personal", "Personal")]
    mounts = attach.active_workspaces(root, "7")
    assert all(m.desk for m in mounts if m.primary)


def test_the_model_is_told_the_desk_is_personal_and_the_repo_is_biz():
    from worker.engine import mounts_preamble
    text = mounts_preamble([
        {"slug": "biz-b1c87fc32ecb", "path": "/w/.attached/2/biz-b1c87fc32ecb", "role": "private",
         "write": True, "primary": True, "desk": False, "name": "biz"},
        {"slug": "biz-864aed18", "path": "/w/2", "role": "private", "write": True, "primary": False,
         "desk": True, "name": "Personal"},
    ], target="biz-b1c87fc32ecb")
    biz = next(line for line in text.splitlines() if "/w/.attached/2/biz-b1c87fc32ecb" in line)
    desk = next(line for line in text.splitlines() if "`/w/2`" in line)
    assert "**biz** (slug `biz-b1c87fc32ecb`)" in biz and "PERSONAL" not in biz and "this chat's target" in biz
    assert "**Personal** (slug `personal`)" in desk and "PERSONAL" in desk
    assert "biz-864aed18" not in text


def test_a_personal_read_is_the_desk_not_the_first_mount(user2):
    from control_plane.dispatch import _mount_dict
    mounts = attach.chat_read_mounts(user2, "2", ["personal", "biz-b1c87fc32ecb"], None)
    dicts = [_mount_dict(m) for m in mounts]
    desk = [d for d in dicts if d["desk"]]
    assert len(desk) == 1 and desk[0]["path"].endswith("/2") and desk[0]["name"] == "Personal"
    assert next(d for d in dicts if d["slug"] == "biz-b1c87fc32ecb")["name"] == "biz"
