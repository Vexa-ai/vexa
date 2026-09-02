"""Workspace identity: minting, the registry, the migration, and the one rule attach has to keep.

PRD decision 26.1 + 26.5. The tests are grouped by the claim they defend rather than by module,
because the claims are what the decision is made of:

  * an id is minted once and is 10 base32 chars;
  * it lives in the tree, so every MOVE preserves it — park, restore, promote, clone;
  * a repo that already carries one keeps ITS id (an attached repo is the SAME workspace);
  * the registry answers "where is it now" and survives being rebuilt from the volume;
  * a name lives ONLY in the registry, so a rename moves nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_plane import workspace_ids as ids
from shared import workspace_id as wsid


def _ws(root: Path, name: str, *, members: bool = False) -> Path:
    d = root / name
    (d / "kg" / "entities").mkdir(parents=True, exist_ok=True)
    if members:
        (d / "policy").mkdir(parents=True, exist_ok=True)
        (d / "policy" / "members.json").write_text('[{"subject": "owner1", "role": "owner"}]')
    return d


# ── the id itself ────────────────────────────────────────────────────────────────────────────────

def test_minted_id_is_ten_chars_of_base32_and_unique():
    seen = {wsid.mint_id() for _ in range(2000)}
    assert len(seen) == 2000
    for i in seen:
        assert len(i) == wsid.ID_LEN == 10
        assert set(i) <= set("abcdefghijklmnopqrstuvwxyz234567")
        assert wsid.is_workspace_id(i)


@pytest.mark.parametrize("bad", ["", "short", "TOOLONGFORTHIS", "0123456789", "abcdefghi1", "abcdefghij0"])
def test_non_ids_are_rejected(bad):
    assert not wsid.is_workspace_id(bad)


def test_the_file_carries_id_kind_created_and_nothing_else(tmp_path):
    d = _ws(tmp_path, "desk1")
    rec, minted = wsid.ensure_workspace_json(d, kind="desk", created="2026-09-02")
    assert minted is True
    stored = json.loads((d / wsid.WORKSPACE_JSON).read_text())
    assert set(stored) == {"id", "kind", "created"}          # NOT the name — that is the registry's
    assert stored == rec == {"id": rec["id"], "kind": "desk", "created": "2026-09-02"}


def test_ensure_is_idempotent_and_never_re_mints(tmp_path):
    d = _ws(tmp_path, "desk1")
    first, _ = wsid.ensure_workspace_json(d, kind="desk", created="2026-09-02")
    again, minted = wsid.ensure_workspace_json(d, kind="desk", created="2026-09-09")
    assert minted is False
    assert again["id"] == first["id"] and again["created"] == "2026-09-02"


def test_a_corrupt_identity_file_reads_as_no_identity(tmp_path):
    d = _ws(tmp_path, "desk1")
    (d / wsid.VEXA_DIR).mkdir(parents=True, exist_ok=True)
    (d / wsid.WORKSPACE_JSON).write_text("{not json")
    assert wsid.read_workspace_json(d) is None
    rec, minted = wsid.ensure_workspace_json(d, kind="desk", created="2026-09-02")
    assert minted and wsid.is_workspace_id(rec["id"])


def test_a_bad_kind_is_refused(tmp_path):
    with pytest.raises(wsid.WorkspaceIdError):
        wsid.write_workspace_json(_ws(tmp_path, "d"), id=wsid.mint_id(), kind="folder", created="2026-09-02")


# ── classification, off the files ────────────────────────────────────────────────────────────────

def test_classify_reads_the_roster_not_the_name(tmp_path):
    assert ids.classify(_ws(tmp_path, "_global")) == "global"
    assert ids.classify(_ws(tmp_path, "126")) == "desk"
    assert ids.classify(_ws(tmp_path, "aswf-dna-project-b7b2ee", members=True)) == "group"
    # the shape of the live instance's names is NOT the rule: a numeric group and a worded desk
    assert ids.classify(_ws(tmp_path, "42", members=True)) == "group"
    assert ids.classify(_ws(tmp_path, "olgas-notes")) == "desk"


# ── the registry ─────────────────────────────────────────────────────────────────────────────────

def test_registry_round_trips_by_id_and_by_slug(tmp_path):
    reg = ids.WorkspaceRegistry()
    _ws(tmp_path, "126")
    rec = ids.sync_workspace(tmp_path, "126", registry=reg, owner="126")
    assert reg.get(rec["id"])["slug"] == "126"
    assert reg.by_slug("126")["id"] == rec["id"]
    assert reg.all() == [reg.get(rec["id"])]


def test_a_desk_is_never_named_by_its_bare_subject_id(tmp_path):
    """F49: the chat header showed `126` — the directory name showing through."""
    reg = ids.WorkspaceRegistry()
    _ws(tmp_path, "126")
    rec = ids.sync_workspace(tmp_path, "126", registry=reg, owner="126")
    assert rec["name"] != "126"
    assert rec["name"] == "Desk 126"


def test_rename_moves_the_name_and_nothing_else(tmp_path):
    reg = ids.WorkspaceRegistry()
    _ws(tmp_path, "aswf-dna-project-b7b2ee", members=True)
    rec = ids.sync_workspace(tmp_path, "aswf-dna-project-b7b2ee", registry=reg, name="ASWF DNA Project")
    renamed = ids.rename(reg, rec["id"], "Digital Naming Authority")
    assert renamed["id"] == rec["id"] and renamed["slug"] == rec["slug"] and renamed["dir"] == rec["dir"]
    assert renamed["name"] == "Digital Naming Authority"
    # and a re-sync afterwards does NOT undo it
    again = ids.sync_workspace(tmp_path, "aswf-dna-project-b7b2ee", registry=reg)
    assert again["name"] == "Digital Naming Authority"


# ── the migration ────────────────────────────────────────────────────────────────────────────────

def test_migration_gives_every_live_workspace_an_id(tmp_path):
    """The live instance's own shape: `_global`, desks 126/127/128, one group."""
    for name in ("_global", "126", "127", "128"):
        _ws(tmp_path, name)
    _ws(tmp_path, "aswf-dna-project-b7b2ee", members=True)
    _ws(tmp_path, "_system")                                    # private tier — never gets an id
    (tmp_path / ".attached" / "126" / "seed").mkdir(parents=True)

    reg = ids.WorkspaceRegistry()
    out = ids.migrate(tmp_path, reg)

    indexed = {r["slug"]: r for r in out["indexed"]}
    assert set(indexed) == {"_global", "126", "127", "128", "aswf-dna-project-b7b2ee"}
    assert indexed["_global"]["kind"] == "global"
    assert indexed["126"]["kind"] == "desk" and indexed["126"]["owner"] == "126"
    assert indexed["aswf-dna-project-b7b2ee"]["kind"] == "group"
    assert not (tmp_path / "_system" / wsid.WORKSPACE_JSON).exists()
    # every id was WRITTEN into its tree, not merely handed out
    for slug, rec in indexed.items():
        assert wsid.read_workspace_json(tmp_path / slug)["id"] == rec["id"]
    # the parked tree got an id too — so it survives the swap that brings it back
    assert [p["slug"] for p in out["parked_minted"]] == ["seed"]


def test_the_identity_is_committed_by_pathspec_when_it_is_minted(tmp_path):
    """Left uncommitted it would be swept into the next agent turn's commit — the index is a write
    surface with no owner — and a workspace whose id is only in the working tree loses it to any
    operation that rebuilds from HEAD."""
    import subprocess

    d = _ws(tmp_path, "126")
    for args in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "commit", "-q", "-m", "seed", "--allow-empty"],
                   check=True, capture_output=True)
    (d / "kg" / "notes.md").write_text("a concurrent writer's staged work\n")
    subprocess.run(["git", "-C", str(d), "add", "kg/notes.md"], check=True, capture_output=True)

    ids.sync_workspace(tmp_path, "126", registry=ids.WorkspaceRegistry(), owner="126")

    log = subprocess.run(["git", "-C", str(d), "log", "-1", "--name-only", "--format=%s"],
                         capture_output=True, text=True).stdout
    assert "workspace identity" in log
    assert wsid.WORKSPACE_JSON in log
    assert "kg/notes.md" not in log          # by pathspec — the other writer keeps their lane
    assert subprocess.run(["git", "-C", str(d), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip().startswith("A ")


def test_a_workspace_that_is_not_a_repo_still_gets_its_id(tmp_path):
    _ws(tmp_path, "126")
    rec = ids.sync_workspace(tmp_path, "126", registry=ids.WorkspaceRegistry(), owner="126")
    assert wsid.read_workspace_json(tmp_path / "126")["id"] == rec["id"]


def test_migration_is_idempotent(tmp_path):
    _ws(tmp_path, "126")
    reg = ids.WorkspaceRegistry()
    first = ids.migrate(tmp_path, reg)
    second = ids.migrate(tmp_path, reg)
    assert len(first["minted"]) == 1 and second["minted"] == []
    assert first["indexed"][0]["id"] == second["indexed"][0]["id"]


def test_the_registry_is_rebuildable_from_the_volume(tmp_path):
    """A redis loss costs the display NAMES and nothing else — the claim the module makes."""
    _ws(tmp_path, "126")
    _ws(tmp_path, "grp", members=True)
    before = {r["slug"]: r["id"] for r in ids.migrate(tmp_path, ids.WorkspaceRegistry())["indexed"]}
    after = {r["slug"]: r["id"] for r in ids.migrate(tmp_path, ids.WorkspaceRegistry())["indexed"]}
    assert before == after


# ── the move: park · restore · promote · clone ───────────────────────────────────────────────────

def test_id_survives_a_park_and_restore(tmp_path):
    """A swap is a directory move, so the id travels in the tree — the whole preservation rule."""
    import shutil

    reg = ids.WorkspaceRegistry()
    live = _ws(tmp_path, "126")
    rec = ids.sync_workspace(tmp_path, "126", registry=reg, owner="126")
    parked = tmp_path / ".attached" / "126" / "seed"
    parked.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(live), str(parked))                 # park
    _ws(tmp_path, "126")                                # a different tree takes the slot
    other = ids.sync_workspace(tmp_path, "126", registry=reg, owner="126")
    assert other["id"] != rec["id"]
    shutil.rmtree(tmp_path / "126")
    shutil.move(str(parked), str(tmp_path / "126"))     # swap back
    back = ids.sync_workspace(tmp_path, "126", registry=reg, owner="126")
    assert back["id"] == rec["id"]
    assert reg.get(rec["id"])["dir"] == str(tmp_path / "126")


def test_a_cloned_repo_that_already_carries_an_id_keeps_it(tmp_path):
    """Decision 26.5 — that is what makes an ATTACHED repo the same workspace, not a lookalike."""
    reg = ids.WorkspaceRegistry()
    incoming = _ws(tmp_path / "elsewhere", "repo")
    wsid.write_workspace_json(incoming, id="k4m5x2q7bd", kind="group", created="2026-05-01")
    import shutil
    shutil.move(str(incoming), str(tmp_path / "team-repo"))
    rec = ids.sync_workspace(tmp_path, "team-repo", registry=reg, kind="group", name="Team")
    assert rec["id"] == "k4m5x2q7bd" and rec["created"] == "2026-05-01"


def test_promotion_relabels_the_kind_but_never_the_id(tmp_path):
    reg = ids.WorkspaceRegistry()
    d = _ws(tmp_path, "workspace-1")
    rec = ids.sync_workspace(tmp_path, "workspace-1", registry=reg, owner="126")
    assert rec["kind"] == "desk"
    (d / "policy").mkdir(exist_ok=True)
    (d / "policy" / "members.json").write_text('[{"subject": "126", "role": "owner"}]')
    promoted = ids.sync_workspace(tmp_path, "workspace-1", registry=reg)
    assert promoted["kind"] == "group" and promoted["id"] == rec["id"]
    assert wsid.read_workspace_json(d)["kind"] == "group"


# ── access ───────────────────────────────────────────────────────────────────────────────────────

def _member_of(*pairs):
    allowed = set(pairs)
    return lambda root, slug, subject: "contributor" if (slug, subject) in allowed else None


def test_access_states(tmp_path):
    reg = ids.WorkspaceRegistry()
    for n in ("_global", "126", "127"):
        _ws(tmp_path, n)
    _ws(tmp_path, "grp", members=True)
    ids.migrate(tmp_path, reg)
    g = reg.by_slug("_global")
    desk126, desk127, grp = reg.by_slug("126"), reg.by_slug("127"), reg.by_slug("grp")
    member = _member_of(("grp", "126"))

    assert ids.access_for(g, "126") == ids.ACCESS_READABLE            # the org tier is everyone's
    assert ids.access_for(desk126, "126") == ids.ACCESS_READABLE      # my own desk
    assert ids.access_for(desk127, "126") == ids.ACCESS_NOT_YOURS     # somebody else's desk
    assert ids.access_for(grp, "126", root=tmp_path, is_member=member) == ids.ACCESS_READABLE
    assert ids.access_for(grp, "127", root=tmp_path, is_member=member) == ids.ACCESS_NOT_YOURS
    assert ids.access_for(None, "126") == ids.ACCESS_GONE


def test_a_deleted_tree_is_gone_not_not_yours(tmp_path):
    import shutil

    reg = ids.WorkspaceRegistry()
    _ws(tmp_path, "126")
    rec = ids.sync_workspace(tmp_path, "126", registry=reg, owner="126")
    shutil.rmtree(tmp_path / "126")
    assert ids.access_for(reg.get(rec["id"]), "126") == ids.ACCESS_GONE
    # and the LAST KNOWN NAME still comes back, which is what renders in a dead link
    assert ids.view(reg.get(rec["id"]), ids.ACCESS_GONE)["name"] == "Desk 126"


def test_a_membership_check_that_raises_is_not_yours_never_a_500(tmp_path):
    reg = ids.WorkspaceRegistry()
    _ws(tmp_path, "grp", members=True)
    rec = ids.sync_workspace(tmp_path, "grp", registry=reg)

    def boom(root, slug, subject):
        raise RuntimeError("policy store unreadable")

    assert ids.access_for(rec, "126", root=tmp_path, is_member=boom) == ids.ACCESS_NOT_YOURS
