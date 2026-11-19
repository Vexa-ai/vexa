"""agent-api's chat history reader never reads another subject's chat.

A chat's pointer, its user_text sidecar and its transcript sit under ``<ws>/.claude/`` — in
``_system`` above all, which the model's tools may write during a turn. agent-api reads them while it
can see every subject's store, so a link planted there (at the file, at ``sessions``, at ``.claude``
or at a transcript folder) is never followed; a pointer that is no session id is no pointer.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

def _history_world(tmp_path: Path):
    """Two subjects in one store. ``u_victim`` has a real chat; ``u_attacker``'s model planted links
    in its own continuity folder pointing at the victim's."""
    from control_plane.workspace_reader import WorkspaceReader

    root = tmp_path / "store"
    victim = root / "u_victim"
    (victim / ".claude" / "sessions").mkdir(parents=True)
    (victim / ".claude" / "sessions" / "main.session").write_text("sid-victim\n")
    (victim / ".claude" / "sessions" / "main.turns.jsonl").write_text(
        json.dumps({"key": "k", "user_text": "VICTIM-PRIVATE-WORDS"}) + "\n")
    proj = victim / ".claude" / "projects" / "-victim-slug"
    proj.mkdir(parents=True)
    proj.joinpath("sid-victim.jsonl").write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": "VICTIM-TRANSCRIPT"}}) + "\n")
    attacker = root / "u_attacker"
    (attacker / ".claude" / "sessions").mkdir(parents=True)
    (attacker / ".claude" / "projects").mkdir(parents=True)
    return WorkspaceReader(str(root)), victim, attacker


@pytest.mark.parametrize("planted", ["pointer", "sessions", ".claude"])
def test_history_never_reads_another_subject_through_a_link(tmp_path, planted):
    reader, victim, attacker = _history_world(tmp_path)
    if planted == "pointer":
        (attacker / ".claude" / "sessions" / "main.session").symlink_to(
            victim / ".claude" / "sessions" / "main.session")
        (attacker / ".claude" / "sessions" / "main.turns.jsonl").symlink_to(
            victim / ".claude" / "sessions" / "main.turns.jsonl")
        (attacker / ".claude" / "projects" / "stolen").symlink_to(
            victim / ".claude" / "projects" / "-victim-slug", target_is_directory=True)
    elif planted == "sessions":
        shutil.rmtree(attacker / ".claude" / "sessions")
        (attacker / ".claude" / "sessions").symlink_to(victim / ".claude" / "sessions",
                                                       target_is_directory=True)
    else:
        shutil.rmtree(attacker / ".claude")
        (attacker / ".claude").symlink_to(victim / ".claude", target_is_directory=True)
    seen = json.dumps(reader.history("u_attacker", "main"))
    assert "VICTIM" not in seen
    assert "VICTIM-TRANSCRIPT" in json.dumps(reader.history("u_victim", "main"))


def test_history_never_follows_a_transcript_folder_or_file_link(tmp_path):
    """Even with the attacker's own pointer naming the victim's session id."""
    reader, victim, attacker = _history_world(tmp_path)
    (attacker / ".claude" / "sessions" / "main.session").write_text("sid-victim\n")
    (attacker / ".claude" / "projects" / "stolen").symlink_to(
        victim / ".claude" / "projects" / "-victim-slug", target_is_directory=True)
    own = attacker / ".claude" / "projects" / "-own"
    own.mkdir()
    (own / "sid-victim.jsonl").symlink_to(victim / ".claude" / "projects" / "-victim-slug" / "sid-victim.jsonl")
    assert "VICTIM" not in json.dumps(reader.history("u_attacker", "main"))


def test_history_ignores_a_pointer_that_is_no_session_id(tmp_path):
    reader, _victim, attacker = _history_world(tmp_path)
    (attacker / ".claude" / "sessions" / "main.session").write_text("../u_victim/x\n")
    assert reader.history("u_attacker", "main") == []
