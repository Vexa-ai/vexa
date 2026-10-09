"""L2: what a workspace's `skills/` can put into the worker's skill stage.

A workspace's files come from an imported repository or from the model's own tools, and the worker
that stages them may run as root. Every test here plants something outside the workspace (a file
standing for anything the worker can read and the model's tools cannot) and asserts its bytes never
reach the stage, while the workspace's ordinary skills still stage as before. The last test holds
the skills the platform ships — linked as shipped, never sanitized — to the same rule the sanitizer
enforces on a workspace's.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from llm.claude_skills import (
    WORKSPACE_SKILL_DROPPED_KEYS,
    _frontmatter_key,
    _link_skills_into_home,
)

REPO = Path(__file__).resolve().parents[3]
SECRET = "outside-the-workspace-3c1f"


def _workspace(tmp_path: Path) -> Path:
    """A workspace with one ordinary skill (with a script and a nested reference) beside whatever
    the test plants."""
    work = tmp_path / "ws"
    good = work / "skills" / "good"
    (good / "scripts").mkdir(parents=True)
    (good / "references" / "deep").mkdir(parents=True)
    (good / "SKILL.md").write_text("---\nname: good\ndescription: Fine.\n---\nbody\n")
    (good / "scripts" / "run.sh").write_text("echo hi\n")
    os.chmod(good / "scripts" / "run.sh", 0o755)
    (good / "references" / "deep" / "notes.md").write_text("notes\n")
    return work


def _outside(tmp_path: Path) -> Path:
    """Something the workspace must not be able to stage: a file, a skill folder and a skills
    folder, all outside it."""
    out = tmp_path / "outside"
    (out / "skills" / "foreign").mkdir(parents=True)
    (out / "secret.txt").write_text(SECRET)
    (out / "SKILL.md").write_text(f"---\nname: foreign\n---\n{SECRET}\n")
    (out / "skills" / "foreign" / "SKILL.md").write_text(f"---\nname: foreign\n---\n{SECRET}\n")
    (out / "skills" / "foreign" / "data.txt").write_text(SECRET)
    return out


def _stage(tmp_path: Path, work: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(tmp_path / "no-seed"))
    _link_skills_into_home(work)
    stage = home / ".claude" / "skills"
    assert stage.is_symlink() and stage.resolve().parent == (home / ".vexa-skills").resolve()
    return stage


def _staged_bytes(stage: Path) -> str:
    """Everything readable in the stage, every link inside it followed, concatenated."""
    seen = []
    for dirpath, _dirnames, filenames in os.walk(stage.resolve(), followlinks=True):
        for name in filenames:
            try:
                seen.append(Path(dirpath, name).read_bytes().decode("utf-8", "replace"))
            except OSError:
                pass
    return "\n".join(seen)


def _assert_good_skill_staged(stage: Path) -> None:
    good = stage / "good"
    assert not good.is_symlink() and good.is_dir()
    assert yaml.safe_load((good / "SKILL.md").read_text().split("---\n", 2)[1]) == {
        "name": "good", "description": "Fine."}
    assert (good / "scripts" / "run.sh").read_text() == "echo hi\n"
    assert os.access(good / "scripts" / "run.sh", os.X_OK)
    assert (good / "references" / "deep" / "notes.md").read_text() == "notes\n"
    for dirpath, dirnames, filenames in os.walk(good):
        for name in dirnames + filenames:
            assert not Path(dirpath, name).is_symlink(), f"{name} is staged as a link"


def test_a_skills_folder_that_is_a_link_stages_none_of_the_workspaces_skills(tmp_path, monkeypatch):
    out = _outside(tmp_path)
    work = tmp_path / "ws"
    work.mkdir()
    (work / "skills").symlink_to(out / "skills", target_is_directory=True)
    stage = _stage(tmp_path, work, monkeypatch)
    assert list(stage.iterdir()) == []
    assert SECRET not in _staged_bytes(stage)


def test_a_skill_folder_that_is_a_link_is_not_staged(tmp_path, monkeypatch):
    out = _outside(tmp_path)
    work = _workspace(tmp_path)
    (work / "skills" / "foreign").symlink_to(out / "skills" / "foreign", target_is_directory=True)
    stage = _stage(tmp_path, work, monkeypatch)
    assert sorted(p.name for p in stage.iterdir()) == ["good"]
    assert SECRET not in _staged_bytes(stage)
    _assert_good_skill_staged(stage)


def test_a_skill_md_that_links_out_of_the_workspace_is_not_staged(tmp_path, monkeypatch):
    out = _outside(tmp_path)
    work = _workspace(tmp_path)
    (work / "skills" / "linked").mkdir()
    (work / "skills" / "linked" / "SKILL.md").symlink_to(out / "SKILL.md")
    (work / "skills" / "linked" / "readme.txt").write_text("ordinary\n")
    stage = _stage(tmp_path, work, monkeypatch)
    assert sorted(p.name for p in stage.iterdir()) == ["good"]
    assert SECRET not in _staged_bytes(stage)
    _assert_good_skill_staged(stage)


def test_a_sibling_link_is_left_out_and_the_rest_of_the_skill_stages(tmp_path, monkeypatch):
    out = _outside(tmp_path)
    work = _workspace(tmp_path)
    good = work / "skills" / "good"
    (good / "secret.txt").symlink_to(out / "secret.txt")
    (good / "outside").symlink_to(out, target_is_directory=True)
    (good / "references" / "deep" / "more.txt").symlink_to(out / "secret.txt")
    (good / "dangling").symlink_to(tmp_path / "nowhere")
    stage = _stage(tmp_path, work, monkeypatch)
    assert not (stage / "good" / "secret.txt").exists()
    assert not (stage / "good" / "outside").exists()
    assert not (stage / "good" / "references" / "deep" / "more.txt").exists()
    assert not os.path.lexists(stage / "good" / "dangling")
    assert SECRET not in _staged_bytes(stage)
    _assert_good_skill_staged(stage)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_fifo_sibling_is_left_out_without_blocking_the_turn(tmp_path, monkeypatch):
    work = _workspace(tmp_path)
    os.mkfifo(work / "skills" / "good" / "pipe")
    os.mkfifo(work / "skills" / "good" / "references" / "pipe")
    stage = _stage(tmp_path, work, monkeypatch)
    assert not os.path.lexists(stage / "good" / "pipe")
    assert not os.path.lexists(stage / "good" / "references" / "pipe")
    _assert_good_skill_staged(stage)


def test_a_hard_link_is_left_out(tmp_path, monkeypatch):
    out = _outside(tmp_path)
    work = _workspace(tmp_path)
    os.link(out / "secret.txt", work / "skills" / "good" / "secret.txt")
    stage = _stage(tmp_path, work, monkeypatch)
    assert not (stage / "good" / "secret.txt").exists()
    assert SECRET not in _staged_bytes(stage)
    _assert_good_skill_staged(stage)


@pytest.mark.parametrize("nested", ["references/SKILL.md", "references/deep/SKILL.md",
                                    "references/skill.md", "scripts/SKILL.md/x"])
def test_a_skill_holding_a_nested_skill_md_is_not_staged(tmp_path, monkeypatch, nested):
    work = _workspace(tmp_path)
    hidden = work / "skills" / "hidden"
    (hidden / "references").mkdir(parents=True)
    (hidden / "scripts").mkdir()
    (hidden / "SKILL.md").write_text("---\nname: hidden\n---\nlooks fine\n")
    path = hidden / nested
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: inner\nallowed-tools: Bash\n---\n{SECRET}\n")
    stage = _stage(tmp_path, work, monkeypatch)
    assert sorted(p.name for p in stage.iterdir()) == ["good"]
    assert SECRET not in _staged_bytes(stage)
    _assert_good_skill_staged(stage)


def test_a_staged_skill_is_a_copy_the_workspace_cannot_change(tmp_path, monkeypatch):
    """Once staged, swapping a workspace file for a link (or editing it) changes nothing staged."""
    out = _outside(tmp_path)
    work = _workspace(tmp_path)
    stage = _stage(tmp_path, work, monkeypatch)
    script = work / "skills" / "good" / "scripts" / "run.sh"
    script.unlink()
    script.symlink_to(out / "secret.txt")
    (work / "skills" / "good" / "references" / "deep" / "notes.md").write_text(SECRET)
    assert SECRET not in _staged_bytes(stage)
    _assert_good_skill_staged(stage)


def test_a_workspace_skill_past_the_turns_budget_is_not_staged(tmp_path, monkeypatch):
    import llm.claude_skills as skills

    work = _workspace(tmp_path)
    big = work / "skills" / "big"
    big.mkdir()
    (big / "SKILL.md").write_text("---\nname: big\n---\nbody\n")
    (big / "data.bin").write_bytes(b"x" * 4096)
    monkeypatch.setattr(skills, "WORKSPACE_SKILLS_MAX_BYTES", 2048)
    stage = _stage(tmp_path, work, monkeypatch)
    assert sorted(p.name for p in stage.iterdir()) == ["good"]
    _assert_good_skill_staged(stage)


def _shipped_skills() -> list[Path]:
    """Every skill the worker loads as shipped: each template under `behavior/workspaces/` is copied
    into the worker images as a seed (`workspace-seeds/`), and its `skills/` is the platform's set."""
    found = sorted((REPO / "behavior" / "workspaces").glob("*/skills/**/SKILL.md"))
    found += sorted((REPO / "core" / "agent" / "tools-seed").rglob("SKILL.md"))
    return found


def test_no_shipped_platform_skill_grants_tools_or_registers_hooks():
    """The platform's skills are linked into the stage as shipped, past the sanitizer, so none may
    carry a key the sanitizer would drop from a workspace's — compared the way the sanitizer
    compares them — and each one's frontmatter must close and parse to a mapping."""
    shipped = _shipped_skills()
    assert shipped, "no shipped skills found; the seed layout moved"
    for path in shipped:
        text = path.read_text(encoding="utf-8").lstrip("﻿")
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            continue
        close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        assert close is not None, f"{path}: frontmatter does not close"
        meta = yaml.safe_load("".join(lines[1:close])) or {}
        assert isinstance(meta, dict), f"{path}: frontmatter is not a mapping"
        dropped = {k for k in meta if _frontmatter_key(k) in WORKSPACE_SKILL_DROPPED_KEYS}
        assert not dropped, f"{path.relative_to(REPO)} declares {sorted(map(str, dropped))}"
