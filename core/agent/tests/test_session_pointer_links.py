"""The live chat continuity files in ``_system`` are never read or written through a link.

``_system`` is granted to the model's tools for the turn, and the worker that reads the session
pointer before the turn and writes it back after may run as root. So the pointer
(``.claude/sessions/<session>.session``) and the user_text sidecar beside it
(``<session>.turns.jsonl``) are reached folder by folder without following a link, read only as a
regular file with a single link, and written by creating a new file and renaming it over the name —
a link planted at the name, at ``sessions`` or at ``.claude`` is never read through or written
through.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import worker.engine as engine

SECRET = "outside-the-workspace-5f1c"


class _Harness:
    name = "fake"

    def __init__(self):
        self.asked = []

    def prepare(self, work, chat_root=None):
        pass

    def transcript_bytes(self, work, session_id):
        self.asked.append(session_id)
        return 0

    def preflight(self):
        return None


@pytest.fixture
def roots(tmp_path, monkeypatch):
    work, chat_root, out = tmp_path / "ws", tmp_path / "_system", tmp_path / "outside"
    for d in (work, chat_root, out):
        d.mkdir()
    (out / "target.txt").write_text(SECRET)
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([
        {"slug": "ws", "path": str(work), "role": "private", "write": True, "primary": True},
        {"slug": "_system", "path": str(chat_root), "role": "system", "write": True},
    ]))
    return work, chat_root, out


def _turn(monkeypatch, work, *, sid="s-new", first_ok=True):
    """One turn with no harness underneath: it answers ``done`` with ``sid``."""
    calls = []

    def fake_run_harness_turn(work, prompt, harness, **kw):
        calls.append(kw.get("session"))
        if not first_ok and len(calls) == 1:
            yield {"type": "done", "ok": False}
            return
        yield {"type": "done", "ok": True, "sessionId": sid}

    monkeypatch.setattr(engine, "run_harness_turn", fake_run_harness_turn)
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "report_friction", lambda rec, **kw: None)
    harness = _Harness()
    list(engine.run_turn_over_workspace(work, "hi", harness=harness))
    return calls, harness


def _plant(chat_root: Path, where: str, out: Path) -> None:
    sessions = chat_root / ".claude" / "sessions"
    if where == "pointer":
        sessions.mkdir(parents=True)
        (sessions / "main.session").symlink_to(out / "target.txt")
    elif where == "sessions":
        (chat_root / ".claude").mkdir()
        sessions.symlink_to(out, target_is_directory=True)
    else:
        (chat_root / ".claude").symlink_to(out, target_is_directory=True)


@pytest.mark.parametrize("where", ["pointer", "sessions", ".claude"])
def test_a_planted_link_is_never_read_as_the_pointer(roots, where):
    _work, chat_root, out = roots
    _plant(chat_root, where, out)
    (out / "main.session").write_text(SECRET)          # what a link at a folder would reach
    harness = _Harness()
    sess_file = chat_root / ".claude" / "sessions" / "main.session"
    assert engine._resume_id(chat_root, sess_file, harness) is None
    assert harness.asked == []


@pytest.mark.parametrize("where", ["pointer", "sessions", ".claude"])
def test_a_planted_link_is_never_written_through(roots, monkeypatch, where):
    work, chat_root, out = roots
    _plant(chat_root, where, out)
    before = {p.name: p.read_text() for p in out.iterdir()}
    calls, _ = _turn(monkeypatch, work)
    assert calls == [None]                                # nothing resumed through the link
    assert {p.name: p.read_text() for p in out.iterdir()} == before
    pointer = chat_root / ".claude" / "sessions" / "main.session"
    if where == "pointer":                                # the link is replaced by a real pointer
        assert not pointer.is_symlink() and pointer.read_text() == "s-new"


def test_an_ordinary_pointer_is_read_and_written(roots, monkeypatch):
    work, chat_root, _out = roots
    pointer = chat_root / ".claude" / "sessions" / "main.session"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("s-old\n")
    calls, harness = _turn(monkeypatch, work)
    assert calls == ["s-old"] and harness.asked == ["s-old"]
    assert pointer.read_text() == "s-new" and not pointer.is_symlink()


def test_a_refused_resume_drops_the_pointer_never_through_a_link(roots, monkeypatch):
    work, chat_root, out = roots
    pointer = chat_root / ".claude" / "sessions" / "main.session"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("s-stale")
    calls, _ = _turn(monkeypatch, work, first_ok=False)
    assert calls == ["s-stale", None] and pointer.read_text() == "s-new"
    # and with `sessions` re-pointed outside, the drop removes nothing there
    shutil.rmtree(chat_root / ".claude" / "sessions")
    (out / "main.session").write_text("s-stale")
    (chat_root / ".claude" / "sessions").symlink_to(out, target_is_directory=True)
    _turn(monkeypatch, work, first_ok=False)
    assert (out / "main.session").read_text() == "s-stale"


def test_a_pointer_that_is_no_session_id_is_not_resumed(roots):
    _work, chat_root, _out = roots
    pointer = chat_root / ".claude" / "sessions" / "main.session"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("../../outside/target\n")
    harness = _Harness()
    assert engine._resume_id(chat_root, pointer, harness) is None and harness.asked == []


@pytest.mark.parametrize("where", ["sidecar", "sessions", ".claude"])
def test_the_user_text_sidecar_is_never_read_or_written_through_a_link(roots, where):
    _work, chat_root, out = roots
    sessions = chat_root / ".claude" / "sessions"
    if where == "sidecar":
        sessions.mkdir(parents=True)
        (sessions / "main.turns.jsonl").symlink_to(out / "target.txt")
    else:
        _plant(chat_root, where, out)
    before = {p.name: p.read_text() for p in out.iterdir()}
    engine.record_user_text(chat_root, "main", "composed", "said")      # never raises
    assert {p.name: p.read_text() for p in out.iterdir()} == before
    sidecar = sessions / "main.turns.jsonl"
    if where == "sidecar":
        assert not sidecar.is_symlink()
        lines = sidecar.read_text().splitlines()
        assert [json.loads(ln)["user_text"] for ln in lines] == ["said"]
        assert SECRET not in sidecar.read_text()

