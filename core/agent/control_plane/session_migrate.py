"""session_migrate.py — copy existing chat transcripts out of workspaces into the session store
(ADR-0048 decision 10).

For every person on the volume, every chat thread found in their continuity roots (``_system`` —
``<root>/.system/<subject>`` — then their own workspace) is copied into the store as version 1:
the thread's pointer and turn records under the canonical names, and the transcripts its pointer
names (claude-code / openai-agent under ``.claude/projects``, codex under ``.claude/codex/sessions``).

- **Idempotent.** A thread the store already holds a version of is skipped.
- **Verified.** Every copy is read back from the store and compared byte for byte.
- **Counts only.** The log and the summary carry counts; never a name, a path or a byte of content.
- **Nothing is deleted** unless ``clean=True``, and then only the files of a thread whose read-back
  matched (or, on a later run, whose head is still exactly that copy). Workspaces keep the old layout
  otherwise, which agent-api still reads for one release.
- **Retire** (``--clean --retire``) empties the workspaces of conversations: every old-layout file the
  store carries leaves, and one whose copy diverged from the store's head leaves too. Each is MOVED,
  never deleted, into ``<workspaces>/.sessions-retired/<run>/`` — a dot-folder no mount, person or
  workspace ever names — so the agent cannot find a chat under any mount and nothing is lost.
- **Reversible.** ``--restore`` moves every archived file back to where it was, never over a file
  written since (that one stays archived and is counted).
- **People only.** Threads are read for the people on the volume (``people``): the system tier and
  the personal desks. A shared workspace's directory is never treated as a person.
- **Per thread.** A mismatched read-back is written again, and a session this run created is removed
  if it never matches; a conflict or a store error is counted and the run goes on.

    python -m control_plane.session_migrate [--workspaces /workspaces] [--dry-run] [--clean [--retire]]
    python -m control_plane.session_migrate --restore

The store comes from the same settings agent-api uses — its database (``DB_HOST`` with the session
store's database user and password, or ``VEXA_SESSIONS_DB_URL``) and its transcript store
(``VEXA_SESSIONS_S3_ENDPOINT`` with its access and secret key); without them the command refuses.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from control_plane.session_store import SessionConflict, SessionStore
from workspaces.shared import workspace_paths as wpaths

logger = logging.getLogger("agent_api.session_migrate")

CANONICAL = "conversation"
_PLUMBING = (".claude",)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def continuity_roots(root: Path, subject: str) -> list[Path]:
    out = []
    for c in (root / ".system" / subject, root / subject):
        if c.is_dir() and not c.is_symlink():
            out.append(c)
    return out


def thread_names(roots: list[Path]) -> list[str]:
    names: set = set()
    for r in roots:
        for f in wpaths.list_files_inside(r, ".claude/sessions", suffix=".session", allow=_PLUMBING):
            n = f.rsplit("/", 1)[-1][: -len(".session")]
            if _NAME.fullmatch(n):
                names.add(n)
        if wpaths.is_file_inside(r, ".claude/.session", allow=_PLUMBING):
            names.add("main")
    return sorted(names)


def collect_thread(roots: list[Path], name: str) -> tuple[dict, list[tuple[Path, str]]]:
    """``(files, sources)`` for one thread: the bundle in canonical names, and where each came from."""
    files: dict = {}
    sources: list = []
    pointer: Optional[str] = None
    for r in roots:
        for rel in (f".claude/sessions/{name}.session",) + ((".claude/.session",) if name == "main" else ()):
            text = wpaths.read_text_inside(r, rel, max_bytes=1 << 16, allow=_PLUMBING)
            if text is not None and text.strip():
                pointer = text.strip()
                files[f".claude/sessions/{CANONICAL}.session"] = (pointer + "\n").encode()
                sources.append((r, rel))
                break
        if pointer:
            turns = wpaths.read_bytes_inside(r, f".claude/sessions/{name}.turns.jsonl", allow=_PLUMBING)
            if turns is not None:
                files[f".claude/sessions/{CANONICAL}.turns.jsonl"] = turns
                sources.append((r, f".claude/sessions/{name}.turns.jsonl"))
            break
    if not pointer or not _SID.fullmatch(pointer):
        return {}, []
    for r in roots:
        for slug in wpaths.list_dirs_inside(r, ".claude/projects", allow=_PLUMBING):
            rel = f".claude/projects/{slug}/{pointer}.jsonl"
            raw = wpaths.read_bytes_inside(r, rel, allow=_PLUMBING)
            if raw is not None and rel not in files:
                files[rel] = raw
                sources.append((r, rel))
        for rel in wpaths.walk_files_inside(r, ".claude/codex/sessions", allow=_PLUMBING):
            if pointer in rel.rsplit("/", 1)[-1] and rel not in files:
                raw = wpaths.read_bytes_inside(r, rel, allow=_PLUMBING)
                if raw is not None:
                    files[rel] = raw
                    sources.append((r, rel))
    return files, sources


#: How many times a copy whose read-back differs is written again before it is given up on.
VERIFY_ATTEMPTS = 3


def _is_group(ws: Path) -> bool:
    """A shared workspace, by either of the two records that make one: its member list
    (``workspace_membership.MEMBERS_FILE``) or its identity's kind (``workspace.json``)."""
    from control_plane.workspace_membership import MEMBERS_FILE  # noqa: PLC0415
    from workspaces.shared.workspace_id import read_workspace_json  # noqa: PLC0415

    if wpaths.is_file_inside(ws, MEMBERS_FILE, allow=("policy",)):
        return True
    rec = read_workspace_json(ws) or {}
    return str(rec.get("kind") or "") in ("group", "global")


def people(root: Path) -> list[str]:
    """The people on the volume: everyone with a system tier (``.system/<subject>``), and every
    top-level desk that is not a shared workspace. A shared workspace's directory is never a person,
    so its chats are never filed under its slug — nor deleted by ``--clean``."""
    subjects: set = set()
    sysdir = root / ".system"
    if sysdir.is_dir():
        subjects |= {p.name for p in sysdir.iterdir() if p.is_dir() and not p.is_symlink()}
    for p in root.iterdir():
        if (p.is_dir() and not p.is_symlink() and not p.name.startswith((".", "_"))
                and p.name not in subjects and not _is_group(p)):
            subjects.add(p.name)
    return sorted(subjects)


#: Where ``--retire`` moves old-layout conversation files: a dot-folder at the volume's root. A dot
#: name is never a workspace, a desk or a person (``people``, the workspace name rule), so no mount
#: ever holds it; one sub-folder per run keeps two runs from ever writing the same name.
ARCHIVE = ".sessions-retired"


def old_layout_files(root: "str | Path") -> int:
    """How many conversation files are still in the old layout, under every person's continuity
    roots (``_system`` and their own desk): pointers, turn records and the transcripts they name."""
    root = Path(root)
    n = 0
    for subject in people(root):
        roots = continuity_roots(root, subject)
        for name in thread_names(roots):
            n += len(collect_thread(roots, name)[1])
    return n


def _move_out(root: Path, run: str, r: Path, rel: str) -> bool:
    """Move ``r/<rel>`` into ``<root>/.sessions-retired/<run>/<r relative to root>/<rel>``: written,
    read back equal, and only then removed from the workspace."""
    data = wpaths.read_bytes_inside(r, rel, allow=_PLUMBING)
    if data is None:
        return False
    arch = root / ARCHIVE
    os.close(wpaths.dir_fd_inside(root, [ARCHIVE], create=True, mode=0o700))
    dest = f"{run}/{r.relative_to(root).as_posix()}/{rel}"
    if not wpaths.write_new_inside(arch, dest, data, mode=0o600, allow=_PLUMBING):
        if wpaths.read_bytes_inside(arch, dest, allow=_PLUMBING) != data:
            return False
    if wpaths.read_bytes_inside(arch, dest, allow=_PLUMBING) != data:
        return False
    return wpaths.unlink_inside(r, rel, allow=_PLUMBING)


def retire_cli_transcripts(root: "str | Path", subject: str, *, run: "Optional[str]" = None) -> int:
    """MOVE ``subject``'s raw CLI transcripts out of their private ``_system`` tier — mounted read-write
    into every chat worker of theirs — into the archive (``.sessions-retired/<run>/.system/<subject>/
    .claude/projects/…``), which no worker mounts. Each file is written, read back equal and only then
    removed (``_move_out``); never deleted. The number of files moved.

    Live app.dev 2026-10-15: a chat's shell `cd /workspaces/.system/2` and grepped 37 MB of the
    person's transcripts of EVERY chat — every past turn, tool output and credential that ever passed
    through one — for 14+ minutes. With the session store on a conversation lives in the store and
    reaches its own worker as the turn's bundle, so these files are nobody's working state. The same
    archive as ``--retire``: ``archived_roots`` (forget redacts it, ``chat_read`` falls back to it) and
    ``restore`` already know it."""
    root = Path(root)
    if not _NAME.fullmatch(subject or ""):
        return 0
    home = root / ".system" / subject
    rels = list(wpaths.walk_files_inside(home, ".claude/projects", allow=_PLUMBING))
    if not rels:
        return 0
    run = run or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    moved = 0
    for rel in rels:
        if _move_out(root, run, home, rel):
            moved += 1
    return moved


def _clean(sources, counts, *, archive: "Optional[tuple[Path, str]]" = None) -> None:
    for r, rel in sources:
        if archive is not None:
            if _move_out(archive[0], archive[1], r, rel):
                counts["cleaned_files"] += 1
                counts["archived_files"] += 1
        elif wpaths.unlink_inside(r, rel, allow=_PLUMBING):
            counts["cleaned_files"] += 1


def archived_roots(root: "str | Path", subject: str) -> list:
    """Every archived continuity root of ``subject`` (``<archive>/<run>/.system/<subject>`` and
    ``<archive>/<run>/<subject>``) — what a forget must redact as well as the store."""
    root = Path(root)
    arch = root / ARCHIVE
    if not _NAME.fullmatch(subject or "") or not wpaths.is_dir_inside(root, ARCHIVE):
        return []
    out = []
    for run in sorted(wpaths.list_dirs_inside(arch)):
        for rel in (f"{run}/.system/{subject}", f"{run}/{subject}"):
            if wpaths.is_dir_inside(arch, rel):
                out.append(arch / rel)
    return out


def restore(root: "str | Path") -> dict:
    """Put every archived file back where ``--retire`` took it from, newest run first. A file written
    at that name since is never overwritten: the archived one stays and is counted (``kept_newer``)."""
    root = Path(root)
    arch = root / ARCHIVE
    counts = {"restored": 0, "kept_newer": 0, "failed": 0}
    if not arch.is_dir() or arch.is_symlink():
        return counts
    for run in sorted(wpaths.list_dirs_inside(arch), reverse=True):
        for rel in list(wpaths.walk_files_inside(arch, run, allow=_PLUMBING)):
            back = rel.split("/", 1)[1]
            data = wpaths.read_bytes_inside(arch, rel, allow=_PLUMBING)
            if data is None:
                counts["failed"] += 1
                continue
            try:
                put = wpaths.write_new_inside(root, back, data, mode=0o644, allow=_PLUMBING)
            except (wpaths.PathRefused, OSError):
                counts["failed"] += 1
                continue
            if not put:
                counts["kept_newer"] += 1
                continue
            if wpaths.read_bytes_inside(root, back, allow=_PLUMBING) == data:
                wpaths.unlink_inside(arch, rel, allow=_PLUMBING)
                counts["restored"] += 1
            else:
                counts["failed"] += 1
    logger.info("session restore: %s", json.dumps(counts, sort_keys=True))
    return counts


def migrate(store: SessionStore, root: "str | Path", *, dry_run: bool = False,
            clean: bool = False, retire: bool = False) -> dict:
    """Copy every person's threads; one thread's trouble is counted and the run goes on.

    - A thread already in the store is skipped; with ``clean`` its sources are removed only when the
      store's head is still exactly this copy (nothing has been said since).
    - A read-back that differs is written again (``VERIFY_ATTEMPTS``); if it never matches, a session
      this run created is removed again, so no bad version is left as head and the next run copies
      it afresh. Sources are removed only for a verified thread.
    - A conflict (a live turn saved first) or a store error on one thread is counted, never fatal."""
    root = Path(root)
    counts = {"people": 0, "threads": 0, "copied": 0, "skipped": 0, "verified": 0,
              "mismatched": 0, "retried": 0, "conflicts": 0, "failed": 0, "empty": 0,
              "cleaned_files": 0, "retired": 0, "diverged": 0, "archived_files": 0,
              "readmes_refreshed": 0}
    archive = (root, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())) if (retire and clean and not dry_run) else None
    if archive is not None:
        from control_plane import system_mounts  # noqa: PLC0415
        for subject in people(root):
            if system_mounts.refresh_system_readme(root, subject):
                counts["readmes_refreshed"] += 1
    for subject in people(root):
        roots = continuity_roots(root, subject)
        names = thread_names(roots)
        if not names:
            continue
        counts["people"] += 1
        for name in names:
            counts["threads"] += 1
            try:
                _one(store, subject, name, roots, counts, dry_run=dry_run, clean=clean, retire=retire,
                     archive=archive)
            except SessionConflict:
                counts["conflicts"] += 1
            except Exception as exc:  # noqa: BLE001 — one thread's failure is not the run's
                counts["failed"] += 1
                logger.warning("session migration: a thread failed (%s)", type(exc).__name__)
    logger.info("session migration: %s", json.dumps(counts, sort_keys=True))
    return counts


def _carried(head: dict, files: dict) -> bool:
    """Does the store's head carry every leftover file — the same bytes, or the same bytes and more
    (transcripts and turn records only grow)?"""
    return bool(files) and all(rel in head and head[rel].startswith(data) for rel, data in files.items())


def _one(store: SessionStore, subject: str, name: str, roots: list, counts: dict, *,
         dry_run: bool, clean: bool, retire: bool = False, archive=None) -> None:
    existing = store.by_name(subject, name)
    files, sources = collect_thread(roots, name)
    if existing is not None and existing["version"] > 0:
        counts["skipped"] += 1
        if not (clean and not dry_run and files):
            return
        head = store.load_bundle(existing["id"])["files"]
        if existing["version"] == 1 and head == files:
            _clean(sources, counts, archive=archive)
        elif retire:
            # THE STORE ALREADY HOLDS THIS CONVERSATION (ADR-0048 decision 2): leftovers it carries
            # leave the workspace; one that diverged is counted and kept for the operator, never
            # lost — moved out of every mount into the archive, so no chat is found under a mount.
            if _carried(head, files):
                _clean(sources, counts, archive=archive)
                counts["retired"] += 1
            else:
                counts["diverged"] += 1
                if archive is not None:
                    for r, rel in sources:
                        if _move_out(archive[0], archive[1], r, rel):
                            counts["archived_files"] += 1
        return
    if not files:
        counts["empty"] += 1
        return
    if dry_run:
        counts["copied"] += 1
        return
    row = existing or store.create(subject, name=name)
    created = existing is None
    base = 0
    for attempt in range(VERIFY_ATTEMPTS):
        base = store.save_bundle(row["id"], base, files)
        if attempt == 0:
            counts["copied"] += 1
        else:
            counts["retried"] += 1
        if store.load_bundle(row["id"])["files"] == files:
            counts["verified"] += 1
            if clean:
                _clean(sources, counts, archive=archive)
            return
    counts["mismatched"] += 1
    if created:
        store.delete(row["id"])


def main(argv: "list[str] | None" = None) -> int:  # pragma: no cover — the operator's command
    from control_plane import session_store as store_mod
    from shared.config import load_settings

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--workspaces", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--retire", action="store_true",
                    help="with --clean: move every old-layout conversation file out of the workspaces "
                         "into <workspaces>/.sessions-retired/<run>/ (the store's copy verified first)")
    ap.add_argument("--restore", action="store_true",
                    help="move every archived file back where --retire took it from (never over a newer one)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings()
    if a.restore:
        print(json.dumps(restore(a.workspaces or settings.workspaces_dir), sort_keys=True))
        return 0
    store = store_mod.from_settings(settings)
    if store is None:
        print("session store is not configured; nothing migrated. It is on when agent-api has a "
              "database (DB_HOST with the session store's database user and password, or "
              "VEXA_SESSIONS_DB_URL) and a transcript store (VEXA_SESSIONS_S3_ENDPOINT with "
              "VEXA_SESSIONS_S3_ACCESS_KEY and VEXA_SESSIONS_S3_SECRET_KEY)", file=sys.stderr)
        return 2
    counts = migrate(store, a.workspaces or settings.workspaces_dir, dry_run=a.dry_run, clean=a.clean,
                     retire=a.retire)
    counts["left_in_workspaces"] = old_layout_files(a.workspaces or settings.workspaces_dir)
    print(json.dumps(counts, sort_keys=True))
    return 1 if counts["mismatched"] else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
