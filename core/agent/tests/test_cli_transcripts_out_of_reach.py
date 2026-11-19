"""RAW CLI TRANSCRIPTS ARE IN NO CHAT WORKER'S MOUNT.

Live app.dev, 2026-10-15 (user 2, chat pchat-mv9n3xc1): the chat's shell ran
`cd /workspaces/.system/2; grep … .claude/projects/-workspaces-2/*.jsonl` — 37 MB of the person's raw
CLI transcripts of EVERY chat, left in their private `_system` tier by the layout before the session
store, mounted read-write into every chat worker of theirs. With the store on, a conversation lives
in the store and reaches its own worker as the turn's bundle; nothing a worker can reach by path may
hold one. The deny rules in `llm/claude_code` stay as the second fence.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from control_plane import session_migrate, system_mounts
from control_plane.dispatch import _archive_cli_transcripts, build_mount_set
from shared.config import load_settings
from worker.engine import _adopt_legacy_continuity

SUBJECT = "2"
OTHER_CHAT_SID = "8f0e1d2c-3b4a-4c5d-9e6f-7a8b9c0d1e2f"


def _settings(tmp_path: Path):
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    return load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                         internal_api_secret="s", ui_url="https://app.example.test")


def _legacy_transcripts(system: Path) -> dict[str, str]:
    """What the pre-store layout left: transcripts of several chats, under several cwd slugs."""
    files = {
        f".claude/projects/-workspaces-2/{OTHER_CHAT_SID}.jsonl": '{"type":"user","text":"vxa_not_for_you"}\n',
        ".claude/projects/-workspaces-2/a1.jsonl": '{"type":"assistant"}\n',
        ".claude/projects/-workspaces-biz-567d81/b2.jsonl": '{"type":"user"}\n' * 50,
    }
    for rel, text in files.items():
        p = system / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return {rel: hashlib.sha256(t.encode()).hexdigest() for rel, t in files.items()}


def _transcripts_reachable_from(mounts: list[dict]) -> list[str]:
    """Every raw transcript a worker with these mounts could list or read, by any path under them."""
    found = []
    for m in mounts:
        for dirpath, _dirs, files in os.walk(m["path"], followlinks=True):
            found += [os.path.join(dirpath, f) for f in files
                      if f.endswith(".jsonl") and "/.claude/projects/" in os.path.join(dirpath, f)]
    return found


def test_no_worker_mount_holds_a_raw_transcript_once_the_dispatch_has_run(tmp_path):
    settings = _settings(tmp_path)
    system = system_mounts.system_store_path(settings.workspaces_dir, SUBJECT)
    system_mounts.ensure_system_workspace(settings.workspaces_dir, SUBJECT)
    sums = _legacy_transcripts(system)
    assert _transcripts_reachable_from(build_mount_set(settings, SUBJECT))     # the incident's state

    _archive_cli_transcripts(settings, SUBJECT)

    mounts = build_mount_set(settings, SUBJECT)
    assert _transcripts_reachable_from(mounts) == [], "a chat worker can still reach a transcript"
    # …kept, byte for byte, where no worker has a path — the archive forget redacts and restore
    # knows (`session_migrate.archived_roots`): never deleted
    archive = Path(settings.workspaces_dir) / session_migrate.ARCHIVE
    (kept,) = session_migrate.archived_roots(settings.workspaces_dir, SUBJECT)
    for rel, digest in sums.items():
        assert hashlib.sha256((kept / rel).read_bytes()).hexdigest() == digest
    for m in mounts:
        assert not str(archive.resolve()).startswith(str(Path(m["path"]).resolve()) + os.sep)
    # identity and the tier's own files are untouched
    assert (system / "identity.md").exists()


def test_archiving_twice_moves_nothing_more_and_never_overwrites(tmp_path):
    root = tmp_path / "workspaces"
    system = system_mounts.system_store_path(root, SUBJECT)
    _legacy_transcripts(system)
    assert session_migrate.retire_cli_transcripts(root, SUBJECT, run="T1") == 3
    assert session_migrate.retire_cli_transcripts(root, SUBJECT, run="T1") == 0
    p = system / f".claude/projects/-workspaces-2/{OTHER_CHAT_SID}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("a different body")                           # an older worker wrote it again
    assert session_migrate.retire_cli_transcripts(root, SUBJECT, run="T1") == 0   # never over the archived one
    assert p.read_text() == "a different body"                 # …and never lost: it stays until a new run
    assert session_migrate.retire_cli_transcripts(root, SUBJECT, run="T2") == 1
    assert len(session_migrate.archived_roots(root, SUBJECT)) == 2


def test_a_linked_projects_folder_is_not_followed(tmp_path):
    root = tmp_path / "workspaces"
    system = system_mounts.system_store_path(root, SUBJECT)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("x")
    (system / ".claude").mkdir(parents=True)
    os.symlink(elsewhere, system / ".claude" / "projects")
    assert session_migrate.retire_cli_transcripts(root, SUBJECT) == 0
    assert (elsewhere / "keep.txt").read_text() == "x" and (system / ".claude" / "projects").is_symlink()


def test_with_the_store_on_a_worker_reads_no_other_chat_out_of_a_mount(tmp_path, monkeypatch):
    """The pre-store migrate-on-read copied a thread — pointer AND transcript — out of any mount into
    the turn's chat root. With the store on, the turn's own conversation is its bundle; a mount is
    where another chat's transcript could be, so nothing is read from one."""
    chat_root, system = tmp_path / "carry", tmp_path / "_system"
    chat_root.mkdir()
    _legacy_transcripts(system)
    (system / ".claude" / "sessions").mkdir(parents=True)
    (system / ".claude" / "sessions" / "pchat-other.session").write_text(OTHER_CHAT_SID + "\n")
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([
        {"slug": "_system", "path": str(system), "role": "system", "write": True}]))
    monkeypatch.setenv("VEXA_SESSION_STORE", "1")
    _adopt_legacy_continuity(chat_root, system, "pchat-other")
    assert not any(p.suffix == ".jsonl" for p in chat_root.rglob("*")), "another chat's transcript was copied in"
    assert not (chat_root / ".claude" / "sessions" / "pchat-other.session").exists()
