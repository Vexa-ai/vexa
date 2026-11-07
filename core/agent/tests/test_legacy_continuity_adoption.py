"""L2: adopting a chat thread from where it was recorded before chats anchored to `_system`.

The legacy roots are workspaces the model's tools can write, and the worker that adopts from them
may run as root. The adoption still moves an ordinary thread — pointer and transcript — but takes
nothing through a link, copies nothing that is not a regular file, and never writes through
something already at the name it writes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from worker.engine import _adopt_legacy_continuity, _session_file

SECRET = "outside-the-workspace-9d2e"
SID = "0b6c2a9e-1111-4c2e-9a51-6f1d2b3c4d5e"


def _roots(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    chat_root, work, out = tmp_path / "_system", tmp_path / "ws", tmp_path / "outside"
    for d in (chat_root, work, out):
        d.mkdir()
    (out / "secret.txt").write_text(SECRET)
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([
        {"slug": "ws", "path": str(work), "role": "private", "write": True, "primary": True},
        {"slug": "_system", "path": str(chat_root), "role": "system", "write": True},
    ]))
    return chat_root, work, out


def _legacy_thread(work: Path, session: str = "main", sid: str = SID) -> Path:
    (work / ".claude" / "sessions").mkdir(parents=True)
    (work / ".claude" / "sessions" / f"{session}.session").write_text(sid + "\n")
    transcript = work / ".claude" / "projects" / "-ws" / f"{sid}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"type":"user"}\n')
    return transcript


def _everything_under(root: Path) -> str:
    seen = []
    for dirpath, _dirs, files in os.walk(root, followlinks=True):
        for name in files:
            try:
                seen.append(Path(dirpath, name).read_text("utf-8", "replace"))
            except OSError:
                pass
    return "\n".join(seen)


def test_an_ordinary_legacy_thread_is_adopted(tmp_path, monkeypatch):
    chat_root, work, _out = _roots(tmp_path, monkeypatch)
    _legacy_thread(work)
    _adopt_legacy_continuity(chat_root, work, "main")
    assert (chat_root / ".claude" / "sessions" / "main.session").read_text() == SID + "\n"
    adopted = chat_root / ".claude" / "projects" / "-ws" / f"{SID}.jsonl"
    assert adopted.read_text() == '{"type":"user"}\n' and not adopted.is_symlink()


def test_a_pointer_that_links_out_of_the_workspace_is_not_adopted(tmp_path, monkeypatch):
    chat_root, work, out = _roots(tmp_path, monkeypatch)
    (work / ".claude" / "sessions").mkdir(parents=True)
    (work / ".claude" / "sessions" / "main.session").symlink_to(out / "secret.txt")
    _adopt_legacy_continuity(chat_root, work, "main")
    assert not os.path.lexists(chat_root / ".claude" / "sessions" / "main.session")
    assert SECRET not in _everything_under(chat_root)


def test_a_sessions_folder_that_is_a_link_is_not_adopted_from(tmp_path, monkeypatch):
    chat_root, work, out = _roots(tmp_path, monkeypatch)
    (out / "sessions").mkdir()
    (out / "sessions" / "main.session").write_text(SECRET)
    (work / ".claude").mkdir()
    (work / ".claude" / "sessions").symlink_to(out / "sessions", target_is_directory=True)
    _adopt_legacy_continuity(chat_root, work, "main")
    assert SECRET not in _everything_under(chat_root)


def test_a_transcript_that_links_out_is_not_copied(tmp_path, monkeypatch):
    chat_root, work, out = _roots(tmp_path, monkeypatch)
    transcript = _legacy_thread(work)
    transcript.unlink()
    transcript.symlink_to(out / "secret.txt")
    (work / ".claude" / "projects" / "-linked").symlink_to(out, target_is_directory=True)
    (out / f"{SID}.jsonl").write_text(SECRET)
    _adopt_legacy_continuity(chat_root, work, "main")
    assert (chat_root / ".claude" / "sessions" / "main.session").read_text() == SID + "\n"
    assert SECRET not in _everything_under(chat_root)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_fifo_transcript_is_not_copied_and_does_not_block(tmp_path, monkeypatch):
    chat_root, work, _out = _roots(tmp_path, monkeypatch)
    transcript = _legacy_thread(work)
    transcript.unlink()
    os.mkfifo(transcript)
    _adopt_legacy_continuity(chat_root, work, "main")
    assert not os.path.lexists(chat_root / ".claude" / "projects" / "-ws" / f"{SID}.jsonl")


def test_adoption_never_writes_through_a_link_already_at_its_target(tmp_path, monkeypatch):
    chat_root, work, out = _roots(tmp_path, monkeypatch)
    _legacy_thread(work)
    victim = out / "victim.txt"
    victim.write_text("untouched")
    (chat_root / ".claude" / "sessions").mkdir(parents=True)
    (chat_root / ".claude" / "sessions" / "main.session").symlink_to(tmp_path / "dangling")
    (chat_root / ".claude" / "projects" / "-ws").mkdir(parents=True)
    (chat_root / ".claude" / "projects" / "-ws" / f"{SID}.jsonl").symlink_to(victim)
    _adopt_legacy_continuity(chat_root, work, "main")
    assert not (tmp_path / "dangling").exists()
    assert victim.read_text() == "untouched"


def test_adoption_never_creates_through_a_linked_folder(tmp_path, monkeypatch):
    chat_root, work, out = _roots(tmp_path, monkeypatch)
    _legacy_thread(work)
    (chat_root / ".claude").symlink_to(out, target_is_directory=True)
    _adopt_legacy_continuity(chat_root, work, "main")
    assert sorted(p.name for p in out.iterdir()) == ["secret.txt"]


@pytest.mark.parametrize("sid", ["../../../../outside/secret", "a/b", ".."])
def test_a_session_id_that_is_not_a_plain_name_moves_no_transcript(tmp_path, monkeypatch, sid):
    chat_root, work, out = _roots(tmp_path, monkeypatch)
    (out / "secret.jsonl").write_text(SECRET)
    (work / ".claude" / "sessions").mkdir(parents=True)
    (work / ".claude" / "sessions" / "main.session").write_text(sid + "\n")
    (work / ".claude" / "projects" / "-ws").mkdir(parents=True)
    _adopt_legacy_continuity(chat_root, work, "main")
    assert not (chat_root / ".claude" / "projects").exists()
    assert SECRET not in _everything_under(chat_root)


def test_the_legacy_single_thread_file_is_adopted_only_when_it_is_a_regular_file(tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    (out / "secret.txt").write_text(SECRET)
    work = tmp_path / "ws"
    (work / ".claude").mkdir(parents=True)
    (work / ".claude" / ".session").symlink_to(out / "secret.txt")
    f = _session_file(work, "main")
    assert not os.path.lexists(f)
    (work / ".claude" / ".session").unlink()
    (work / ".claude" / ".session").write_text("LEGACY_SID")
    f = _session_file(work, "main")
    assert f.read_text() == "LEGACY_SID" and not f.is_symlink()
