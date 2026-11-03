"""claude_skills.py — the skills a Claude Code turn can see, staged into the worker's user scope.

The Claude Code CLI reads skills from ``~/.claude/skills`` (``build_argv`` passes
``--setting-sources user``). Each turn this module assembles that set afresh: the platform's
governed skills from the workspace seed this deployment ships, then the workspace's own
``skills/``, copied with the frontmatter keys that could grant tools or register hooks removed.
Part of the Claude Code adapter (``claude_code.ClaudeCodeHarness.prepare`` calls it).

A workspace's files come from wherever its person imported them or the model wrote them, and the
worker that stages them may run as root while the model's tools do not. So nothing below a
workspace's ``skills/`` is reached through a link: every folder and file there is opened by
descriptor without following one, a file is read only once ``fstat`` says it is a regular file
with no other hard link, and what is staged is a copy of the bytes read, which a later change in
the workspace cannot alter.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from llm import workspace_paths as wpaths

_log = logging.getLogger("llm.claude_skills")


def _governed_skills_dir() -> Optional[Path]:
    """The platform's governed skills: the ``skills/`` of the workspace template this deployment
    seeds from — ``VEXA_DEFAULT_TEMPLATE``, stamped by the dispatch — under the image's read-only
    seeds root (``shared.seeding.resolve_seed_dir``). None when the template has none."""
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

#: What the workspace's skills may stage in one turn, all of them together. Every turn copies them,
#: so this bounds that copy; a skill that would pass it is not staged.
WORKSPACE_SKILLS_MAX_FILES = 2000
WORKSPACE_SKILLS_MAX_BYTES = 64 << 20

#: How many folders deep a workspace skill may nest below its own.
WORKSPACE_SKILL_MAX_DEPTH = 16

# Every folder and file below a workspace's ``skills/`` is opened through ``workspace_paths``
# (``open_dir_at`` / ``open_regular_at`` / ``CREATE_NEW``): never through a link, a file only when it
# is a regular file with no other hard link. The budget and the entry rules are this module's own.


def _frontmatter_key(key: object) -> str:
    return str(key).strip().lower().replace("_", "-")


def _sanitized_workspace_skill(text: str) -> "tuple[dict, str] | None":
    """A workspace ``SKILL.md`` rewritten without ``WORKSPACE_SKILL_DROPPED_KEYS``: ``(frontmatter,
    file text)``. The frontmatter is re-emitted from the parsed mapping, so the CLI reads exactly
    the keys kept here, and a file with none gets an empty block ahead of its text. None — the skill
    is not loaded — when the frontmatter does not close or does not parse to a mapping: a parser
    more lenient than this one might still find a grant in it."""
    rest = text.lstrip("﻿")
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
    """A loadable PLATFORM skill folder: ``<name>/SKILL.md``, not hidden, not the CLI's reserved
    ``synced``. The platform's skills ship in the image and are linked as shipped; a workspace's
    own are never reached through a link (``_assemble_skills``)."""
    return _skill_name(d.name) and d.is_dir() and (d / "SKILL.md").is_file()


def _skill_name(name: str) -> bool:
    return not name.startswith(".") and name.lower() != "synced"


class _SkillRefused(Exception):
    """A workspace skill that is not staged at all; the message says why (it goes to the log)."""


class _EntryRefused(Exception):
    """One entry of a workspace skill that is not staged; the rest of the skill still is."""


class _Budget:
    """What is left of ``WORKSPACE_SKILLS_MAX_FILES`` and ``WORKSPACE_SKILLS_MAX_BYTES``."""

    def __init__(self) -> None:
        self.files = WORKSPACE_SKILLS_MAX_FILES
        self.bytes = WORKSPACE_SKILLS_MAX_BYTES


def _kind(entry: "os.DirEntry[str]") -> str:
    """``link``, ``dir``, ``file`` or ``other``, from the entry itself, never following a link."""
    try:
        if entry.is_symlink():
            return "link"
        if entry.is_dir(follow_symlinks=False):
            return "dir"
        if entry.is_file(follow_symlinks=False):
            return "file"
    except OSError:
        pass
    return "other"


def _open_dir(name: str, dir_fd: int) -> int:
    """A descriptor for folder ``name`` under ``dir_fd``, opened without following a link.
    ``FileNotFoundError`` when there is none; ``_EntryRefused`` when it is anything but a folder."""
    try:
        return wpaths.open_dir_at(dir_fd, name)
    except wpaths.PathRefused as exc:
        raise _EntryRefused(str(exc)) from exc


def _read_file(name: str, dir_fd: int, budget: _Budget) -> "tuple[bytes, int]":
    """The bytes and mode of file ``name`` under ``dir_fd``, opened without following a link — and
    only when it is a regular file, so a device or a FIFO is never opened — and read only when
    ``fstat`` says it is still that file, with no other hard link (whose other name could be
    anywhere on the volume). ``FileNotFoundError`` when there is none; ``_EntryRefused`` when it is
    not such a file; ``_SkillRefused`` when it would pass the turn's budget."""
    try:
        fd = wpaths.open_regular_at(dir_fd, name)
    except wpaths.PathRefused as exc:
        raise _EntryRefused(str(exc)) from exc
    try:
        st = os.fstat(fd)
        if budget.files < 1 or st.st_size > budget.bytes:
            raise _SkillRefused("the workspace's skills are past what one turn stages")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            size += len(chunk)
            if size > budget.bytes:
                raise _SkillRefused("the workspace's skills are past what one turn stages")
            chunks.append(chunk)
        budget.files -= 1
        budget.bytes -= size
        return b"".join(chunks), st.st_mode
    finally:
        os.close(fd)


def _write_new(path: Path, data: bytes, mode: int) -> None:
    """Create ``path`` holding ``data``; never opens anything already there."""
    fd = os.open(path, wpaths.CREATE_NEW, mode)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)


def _copy_entries(src_fd: int, dst: Path, budget: _Budget, where: str, depth: int) -> None:
    """Copy one folder of a workspace skill into ``dst``: each regular file as a copy of the bytes
    read (an executable one stays executable; nothing else of its mode is kept), each folder
    recursively. Hidden entries are not staged: a ``.claude-plugin/`` would make the skill folder a
    plugin, which brings its own hooks, agents and MCP servers. A link, a FIFO or any other kind of
    entry is not opened at all; it is left out and logged. A ``SKILL.md`` anywhere but the top of
    the skill refuses the whole skill, since only the top one is sanitized."""
    with os.scandir(src_fd) as it:
        entries = sorted((entry.name, _kind(entry)) for entry in it)
    for name, kind in entries:
        if name.startswith("."):
            continue
        if name.lower() == "skill.md":
            if depth == 0 and name == "SKILL.md":
                continue  # the sanitized copy, written by the caller
            raise _SkillRefused(f"it holds {where}/{name}, a second SKILL.md")
        try:
            if kind == "dir":
                if depth >= WORKSPACE_SKILL_MAX_DEPTH:
                    raise _SkillRefused("its folders nest too deep")
                fd = _open_dir(name, src_fd)
                try:
                    (dst / name).mkdir()
                    _copy_entries(fd, dst / name, budget, f"{where}/{name}", depth + 1)
                finally:
                    os.close(fd)
            elif kind == "file":
                data, mode = _read_file(name, src_fd, budget)
                _write_new(dst / name, data, 0o755 if mode & 0o111 else 0o644)
            elif kind == "link":
                raise _EntryRefused(f"{name!r} is a link")
            else:
                raise _EntryRefused(f"{name!r} is not a regular file or a folder")
        except FileNotFoundError:
            continue  # gone since it was listed
        except _EntryRefused as exc:
            _log.warning("workspace skill entry %r not staged: %s", f"{where}/{name}", exc)


def _stage_workspace_skill(skills_fd: int, name: str, dst: Path, taken: set,
                           budget: _Budget) -> None:
    """Stage workspace skill ``name`` (a folder under ``skills_fd``) as a real folder holding the
    sanitized ``SKILL.md`` and a copy of every other regular file in it (``_copy_entries``): the
    skill's scripts and references resolve, and nothing staged changes with the workspace. A skill
    whose name is taken by a platform skill is skipped. ``_SkillRefused`` when it is not staged for
    a reason worth logging; it then leaves nothing behind, and nothing of the budget spent."""
    files, size, staged = budget.files, budget.bytes, False
    try:
        src = _open_dir(name, skills_fd)
    except FileNotFoundError:
        return
    try:
        try:
            raw, _mode = _read_file("SKILL.md", src, budget)
        except FileNotFoundError:
            return  # a folder without a SKILL.md is not a skill
        except _EntryRefused as exc:
            raise _SkillRefused(str(exc)) from exc
        try:
            sanitized = _sanitized_workspace_skill(raw.decode("utf-8"))
        except UnicodeDecodeError:
            sanitized = None
        if sanitized is None:
            return
        meta, text = sanitized
        if str(meta.get("name") or name) in taken:
            return
        dst.mkdir()
        try:
            _write_new(dst / "SKILL.md", text.encode("utf-8"), 0o644)
            _copy_entries(src, dst, budget, name, 0)
        except BaseException:
            wpaths.remove_tree(dst, ignore_errors=True)
            raise
        staged = True
    finally:
        os.close(src)
        if not staged:
            budget.files, budget.bytes = files, size


def _assemble_skills(stage: Path, work: Path) -> None:
    """The turn's skill set in ``stage``: every platform skill, linked as shipped, then every skill
    in the workspace's own ``skills/`` that does not reuse a platform skill's name, sanitized and
    copied. A seeded workspace keeps a copy of the platform's skills, so on a name clash the
    platform's current version is the one that loads. Nothing of the workspace's is reached through
    a link: a ``skills`` that is not a folder stages none of the workspace's skills, and a skill
    folder or ``SKILL.md`` that is a link stages no skill. A refused skill is logged; the turn runs."""
    taken: set = set()
    platform = _governed_skills_dir()
    if platform is not None:
        for d in sorted(platform.iterdir()):
            if _skill_dir(d):
                (stage / d.name).symlink_to(d, target_is_directory=True)
                taken.add(d.name)
    try:
        own = wpaths.dir_fd_inside(work, ("skills",))
    except FileNotFoundError:
        return
    except (OSError, wpaths.PathRefused) as exc:
        if not os.path.lexists(Path(work) / "skills"):
            return
        _log.warning("workspace skills not staged: %s", exc)
        return
    budget = _Budget()
    try:
        with os.scandir(own) as it:
            entries = sorted((entry.name, _kind(entry)) for entry in it)
        for name, kind in entries:
            if not _skill_name(name) or name in taken:
                continue
            if kind == "link":
                _log.warning("workspace skill %r not staged: it is a link", name)
                continue
            if kind != "dir":
                continue  # a file beside the skills is not one
            try:
                _stage_workspace_skill(own, name, stage / name, taken, budget)
            except (_SkillRefused, _EntryRefused) as exc:
                _log.warning("workspace skill %r not staged: %s", name, exc)
            except Exception:  # noqa: BLE001 — one unreadable skill, not all of them
                _log.warning("workspace skill %r not staged", name, exc_info=True)
                wpaths.remove_tree(stage / name, ignore_errors=True)
    finally:
        os.close(own)


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
                wpaths.remove_tree(old, ignore_errors=True)
    except Exception:  # noqa: BLE001 — best-effort: the turn runs, without skills
        pass
