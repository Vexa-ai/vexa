"""claude_skills.py — the skills a Claude Code turn can see, staged into the worker's user scope.

The Claude Code CLI reads skills from ``~/.claude/skills`` (``build_argv`` passes
``--setting-sources user``). Each turn this module assembles that set afresh: the platform's
governed skills from the workspace seed this deployment ships, then the workspace's own
``skills/``, copied with the frontmatter keys that could grant tools or register hooks removed.
Part of the Claude Code adapter (``claude_code.ClaudeCodeHarness.prepare`` calls it).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import yaml


def _governed_skills_dir() -> Optional[Path]:
    """The platform's governed skills: the ``skills/`` of the workspace seed this deployment ships
    (``shared.seeding.resolve_seed_dir`` — read-only in the image). None when the seed has none."""
    from shared.seeding import resolve_seed_dir

    skills = resolve_seed_dir() / "skills"
    return skills if skills.is_dir() else None


#: Frontmatter a WORKSPACE skill does not carry into the worker. In Claude Code 2.1.293
#: `allowed-tools` is a grant, not a limit: the tools it lists run without a permission check for the
#: turn that invokes the skill, and workspace trust never gates it, so in a headless turn it reaches
#: past `--allowedTools` (a gated MCP tool, the bot verbs a room run withholds, Bash where the turn
#: has none). Measured in the worker image: with `Skill` allowed, a workspace skill's
#: `allowed-tools: Bash` ran Bash under `--allowedTools Read,Skill`. `hooks` registers hooks for the
#: rest of the session; `disableAllHooks` turned them off in the same measurement, and dropping the
#: key keeps that true on its own. A skill declaring either key also needs its own approval before
#: it can be invoked, which a headless turn refuses, so the copy is also what makes it usable. Keys
#: are compared case- and underscore-insensitively, although the CLI reads only the exact names.
WORKSPACE_SKILL_DROPPED_KEYS = frozenset({"allowed-tools", "hooks"})

#: Where the per-turn skill set is assembled, under the worker's HOME. ``~/.claude/skills`` links to
#: the newest ``turn-*`` directory in it; every directory there was made by ``_link_skills_into_home``.
SKILLS_STAGE_DIR = ".vexa-skills"


def _frontmatter_key(key: object) -> str:
    return str(key).strip().lower().replace("_", "-")


def _sanitized_workspace_skill(text: str) -> "tuple[dict, str] | None":
    """A workspace ``SKILL.md`` rewritten without ``WORKSPACE_SKILL_DROPPED_KEYS``: ``(frontmatter,
    file text)``. The frontmatter is re-emitted from the parsed mapping, so the CLI reads exactly
    the keys kept here, and a file with none gets an empty block ahead of its text. None — the skill
    is not loaded — when the frontmatter does not close or does not parse to a mapping: a parser
    more lenient than this one might still find a grant in it."""
    rest = text.lstrip("\ufeff")
    meta: dict = {}
    lines = rest.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if close is None:
            return None
        try:
            parsed = yaml.safe_load("".join(lines[1:close]))
        except Exception:  # noqa: BLE001 — YAMLError, or a ValueError from an impossible date
            return None
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            return None
        meta = {k: v for k, v in parsed.items()
                if _frontmatter_key(k) not in WORKSPACE_SKILL_DROPPED_KEYS}
        rest = "".join(lines[close + 1:])
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False,
                          width=1 << 20)
    return meta, f"---\n{head}---\n{rest}"


def _skill_dir(d: Path) -> bool:
    """A loadable skill folder: ``<name>/SKILL.md``, not hidden, not the CLI's reserved ``synced``."""
    return (not d.name.startswith(".") and d.name.lower() != "synced"
            and d.is_dir() and (d / "SKILL.md").is_file())


def _stage_workspace_skill(src: Path, dst: Path, taken: set) -> None:
    """Stage one workspace skill as a real folder holding the sanitized ``SKILL.md``, with every
    other entry linked back to the workspace so the skill's scripts and references resolve. Hidden
    entries are not linked: a ``.claude-plugin/`` would make the folder a plugin, which brings its
    own hooks, agents and MCP servers. A skill whose name is taken by a platform skill is skipped."""
    try:
        staged = _sanitized_workspace_skill((src / "SKILL.md").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return
    if staged is None:
        return
    meta, text = staged
    if str(meta.get("name") or src.name) in taken:
        return
    dst.mkdir()
    (dst / "SKILL.md").write_text(text, encoding="utf-8")
    for entry in src.iterdir():
        if entry.name != "SKILL.md" and not entry.name.startswith("."):
            (dst / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())


def _assemble_skills(stage: Path, work: Path) -> None:
    """The turn's skill set in ``stage``: every platform skill, linked as shipped, then every skill
    in the workspace's own ``skills/`` that does not reuse a platform skill's name, sanitized. A
    seeded workspace keeps a copy of the platform's skills, so on a name clash the platform's
    current version is the one that loads."""
    taken: set = set()
    platform = _governed_skills_dir()
    if platform is not None:
        for d in sorted(platform.iterdir()):
            if _skill_dir(d):
                (stage / d.name).symlink_to(d, target_is_directory=True)
                taken.add(d.name)
    own = work / "skills"
    if own.is_dir():
        for d in sorted(own.iterdir()):
            try:
                if _skill_dir(d) and d.name not in taken:
                    _stage_workspace_skill(d, stage / d.name, taken)
            except Exception:  # noqa: BLE001 — one unreadable skill, not all of them
                shutil.rmtree(stage / d.name, ignore_errors=True)


def _link_skills_into_home(work: Path) -> None:
    """Expose the platform's skills and the workspace's own through the USER scope, the only
    settings scope the CLI reads (``build_argv`` passes ``--setting-sources user``). Each turn the
    set is assembled afresh in ``~/.vexa-skills/turn-*`` and ``~/.claude/skills`` is repointed at it,
    so a skill removed from the workspace is gone next turn and nothing the CLI writes there (its
    ``synced/`` download, its ``.trash/``) lands in a workspace or in the image's seed. Workspace
    skills are copies made by ``_stage_workspace_skill``: they cannot grant tools or register hooks,
    and an edit made during a turn applies from the next one.

    SAFETY, exactly as ``_link_chat_into_workspace``: only the disposable per-subject HOME may be
    rewritten. Outside a worker (a host test run, a developer shell) ``~/.claude/skills`` holds the
    developer's own skills, so an existing directory is replaced only when EMPTY (``rmdir`` cannot
    destroy content); a non-empty one, or any other object, is left alone and the link is skipped —
    the turn still works, without skills. Earlier stages are removed with ``rmtree``, which unlinks
    the links inside them and never follows one into a workspace. Best-effort: never raises."""
    home = Path(os.environ.get("HOME", "/root"))
    home_claude = home / ".claude"
    link = home_claude / "skills"
    stages = home / SKILLS_STAGE_DIR
    try:
        # The old link goes FIRST, so a turn whose stage cannot be built runs with no skills rather
        # than with whatever an earlier turn, or an earlier version of this function, linked.
        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            if any(link.iterdir()):
                return  # real skills live here — never delete, skip the link
            link.rmdir()
        elif link.exists():
            return
        stages.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="turn-", dir=stages))
        _assemble_skills(stage, work)
        home_claude.mkdir(parents=True, exist_ok=True)
        link.symlink_to(stage, target_is_directory=True)
        for old in stages.iterdir():
            if old != stage and old.name.startswith("turn-") and not old.is_symlink():
                shutil.rmtree(old, ignore_errors=True)
    except Exception:  # noqa: BLE001 — best-effort: the turn runs, without skills
        pass
