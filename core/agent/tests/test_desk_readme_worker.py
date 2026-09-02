"""The worker's three seams for PRD decision 26: the link rule, the ids in context, the desk refresh.

The rule and the ids are one claim in two halves and the halves are useless apart — an agent told to
write `[[ws:<workspace-id>/…]]` and never shown a workspace id will invent one.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from shared import desk_readme
from shared.entities import upsert_entity
from shared.workspace_id import write_workspace_json
from worker import engine


def _ws(root: Path, name: str, wid: str, kind: str = "desk") -> Path:
    d = root / name
    (d / "kg" / "entities").mkdir(parents=True)
    write_workspace_json(d, id=wid, kind=kind, created="2026-09-02")
    return d


def _git_ws(root: Path, name: str, wid: str, kind: str = "desk") -> Path:
    d = _ws(root, name, wid, kind)
    for args in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "commit", "-q", "-m", "seed"], check=True, capture_output=True)
    return d


# ── the rule ─────────────────────────────────────────────────────────────────────────────────────

def test_the_link_rule_names_the_cross_workspace_form():
    text = engine.kg_links_preamble()
    assert "[[ws:<workspace-id>/<entity-id>]]" in text
    assert "[[ws:<workspace-id>/<path>]]" in text
    assert "[[Title]]" in text                      # the in-workspace form is UNCHANGED


def test_the_index_preamble_hands_the_agent_the_ids(tmp_path):
    desk = _ws(tmp_path, "126", "aaaaaaaaaa")
    group = _ws(tmp_path, "grp", "bbbbbbbbbb", kind="group")
    upsert_entity(desk, "person", "Olga Avramenko", ["Attends."], "the meeting")
    upsert_entity(group, "person", "Cottalango Leon", ["Chairs."], "the meeting")

    text = engine.entity_index_preamble([
        {"slug": "126", "path": str(desk), "write": True, "primary": True, "name": "olga@spi.com"},
        {"slug": "grp", "path": str(group), "write": True, "name": "ASWF DNA Project"},
        {"slug": "_global", "path": str(tmp_path), "write": False},
    ])
    assert "workspace id `aaaaaaaaaa`" in text and "(olga@spi.com)" in text
    assert "workspace id `bbbbbbbbbb`" in text and "(ASWF DNA Project)" in text
    assert "Olga Avramenko" in text and "Cottalango Leon" in text


def test_a_workspace_with_no_id_still_gets_its_index(tmp_path):
    """A workspace the migration has not reached must still produce a turn — it just cannot be
    linked TO by id yet."""
    plain = tmp_path / "126"
    (plain / "kg" / "entities").mkdir(parents=True)
    upsert_entity(plain, "person", "Olga Avramenko", ["Attends."], "the meeting")
    text = engine.entity_index_preamble([{"slug": "126", "path": str(plain), "write": True}])
    assert "Olga Avramenko" in text and "workspace id" not in text


# ── which mount is the desk ──────────────────────────────────────────────────────────────────────

def test_desk_mounts_picks_the_primary_and_the_groups(tmp_path):
    mounts = [
        {"slug": "_global", "path": "/g", "role": "global", "write": False},
        {"slug": "126", "path": "/d", "write": True, "primary": True},
        {"slug": "grp", "path": "/s", "role": "shared", "write": True},
        {"slug": "_system", "path": "/y", "role": "system", "write": True},
    ]
    desk, groups = engine.desk_mounts(mounts)
    assert desk["slug"] == "126"
    assert [g["slug"] for g in groups] == ["grp"]


def test_a_room_run_maintains_no_desk(tmp_path):
    """Decision 22: a post-meeting room run writes NO desk — every desk is mounted read-only, so
    there is no primary writable mount and nothing to refresh."""
    mounts = [{"slug": "126", "path": "/d", "write": False, "primary": True},
              {"slug": "127", "path": "/e", "role": "room", "write": False}]
    assert engine.desk_mounts(mounts)[0] is None
    assert engine.refresh_desk_readme(mounts) is None


# ── the refresh ──────────────────────────────────────────────────────────────────────────────────

def test_refresh_writes_the_sections_and_lists_the_groups(tmp_path):
    desk = _git_ws(tmp_path, "126", "aaaaaaaaaa")
    group = _ws(tmp_path, "grp", "bbbbbbbbbb", kind="group")
    upsert_entity(desk, "person", "Olga Avramenko", ["Attends."], "the meeting")

    out = engine.refresh_desk_readme([
        {"slug": "126", "path": str(desk), "write": True, "primary": True},
        {"slug": "grp", "path": str(group), "role": "shared", "write": True, "name": "ASWF DNA Project"},
    ])
    assert out["changed"] is True
    text = (desk / "README.md").read_text()
    assert "[[Olga Avramenko]]" in text
    assert "[[ws:bbbbbbbbbb/README.md]]" in text
    # committed, by pathspec, so a concurrent writer's staged work is not swept in under this message
    log = subprocess.run(["git", "-C", str(desk), "log", "--oneline", "-1", "--name-only"],
                         capture_output=True, text=True).stdout
    assert "README.md" in log and "kg/entities" not in log


def test_refresh_is_idempotent_and_commits_nothing_the_second_time(tmp_path):
    desk = _git_ws(tmp_path, "126", "aaaaaaaaaa")
    upsert_entity(desk, "person", "Olga Avramenko", ["Attends."], "the meeting")
    mounts = [{"slug": "126", "path": str(desk), "write": True, "primary": True}]
    engine.refresh_desk_readme(mounts)
    head = subprocess.run(["git", "-C", str(desk), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert engine.refresh_desk_readme(mounts)["changed"] is False
    assert subprocess.run(["git", "-C", str(desk), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip() == head


def test_refresh_never_touches_what_the_agent_or_the_person_wrote(tmp_path):
    desk = _git_ws(tmp_path, "126", "aaaaaaaaaa")
    header = "# Olga's desk\n\nThe charter is the only thing that matters this quarter.\n"
    (desk / "README.md").write_text(header)
    mounts = [{"slug": "126", "path": str(desk), "write": True, "primary": True}]
    engine.refresh_desk_readme(mounts)
    upsert_entity(desk, "person", "Olga Avramenko", ["Attends."], "the meeting")
    engine.refresh_desk_readme(mounts)
    text = (desk / "README.md").read_text()
    assert text.startswith(header.rstrip("\n"))
    assert "[[Olga Avramenko]]" in text


def test_a_desk_that_is_gone_is_not_an_exception(tmp_path):
    assert engine.refresh_desk_readme([{"slug": "126", "path": str(tmp_path / "nope"),
                                        "write": True, "primary": True}]) is None
    assert engine.refresh_desk_readme([]) is None


def test_every_generated_section_is_present(tmp_path):
    desk = _git_ws(tmp_path, "126", "aaaaaaaaaa")
    engine.refresh_desk_readme([{"slug": "126", "path": str(desk), "write": True, "primary": True}])
    text = (desk / "README.md").read_text()
    for key, heading in desk_readme.SECTIONS:
        assert f"<!-- desk:{key}:start -->" in text and f"## {heading}" in text
