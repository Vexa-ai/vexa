"""ONE NOTE PER MEETING at the turn's commit (`llm.meeting_note_home`, `llm.ports.run_harness_turn`).

A turn in a chat that works in an attached workspace writes the meeting's note with its OWN file
tool — not the page route — into that workspace. On 2026-10-14 that is how four "extended" writes
landed in a copy the meeting view never reads. The commit seam folds the write into the note at
`<root>/<owner>` and removes the stray copy, so the change is committed once, where it is seen.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from llm.meeting_note_home import fold, home_of, nothing_written, redirect_stray_notes
from llm.ports import run_harness_turn

OWNER = "2"
AUTHOR = ("Ada", "ada@example.com")
NOTE = "kg/entities/meeting/2026-10-14-2056-google-meet-meeting-abc-defg-hij.md"
EMPTY = ("---\ntype: meeting\nmeeting: 111\nnative: abc-defg-hij\ntitle: Google Meet meeting\n"
         "date: 2026-10-14\ntranscript_cursor:\n---\n\n<!-- vexa:transcript meeting=111 -->\n")
WRITTEN = EMPTY.replace("transcript_cursor:", "transcript_cursor: 29") + (
    "\n## Decisions\n<!-- meeting:decisions:start -->\n- Ship on Friday\n<!-- meeting:decisions:end -->\n")


def _git(p: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(p), *a], capture_output=True, text=True, check=True).stdout


def _repo(p: Path, files: dict | None = None) -> Path:
    p.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    (p / "README.md").write_text("# ws\n")
    for rel, text in (files or {}).items():
        (p / rel).parent.mkdir(parents=True, exist_ok=True)
        (p / rel).write_text(text)
    _git(p, "add", "-A")
    _git(p, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed")
    return p


class _Writes:
    """The agent's native file tool: writes text at a path, no route in between."""

    def __init__(self, writes):
        self.writes = writes

    def run_turn(self, work, prompt, **_):
        for path, text in self.writes:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text)
        yield {"type": "done", "ok": True, "reply": "extended"}


def _world(tmp_path, attached_files=None):
    root = tmp_path / "workspaces"
    desk = _repo(root / OWNER, {NOTE: EMPTY})
    attached = _repo(root / ".attached" / OWNER / "biz-b1c87f", attached_files)
    return desk, attached


def test_a_direct_file_write_into_the_chats_workspace_lands_in_the_meetings_one_note(tmp_path):
    desk, attached = _world(tmp_path)
    turn = _Writes([(attached / NOTE, WRITTEN)])
    events = list(run_harness_turn(attached, "go", turn, extra_mounts=[desk], author=AUTHOR,
                                   note_home=desk))
    assert (desk / NOTE).read_text() == WRITTEN
    assert not (attached / NOTE).exists(), "a second copy of the note in the chat's workspace"
    assert _git(desk, "status", "--porcelain") == ""            # committed, where the view reads it
    assert NOTE in _git(desk, "log", "-1", "--name-only", "--format=")
    assert NOTE not in _git(attached, "ls-files")
    assert {"type": "note-redirected", "notes": [{"from": "biz-b1c87f", "path": NOTE}]} in events


def test_an_existing_stray_copy_is_folded_in_and_removed(tmp_path):
    # the founder's state: the attached workspace already holds a written copy, the desk the scaffold
    desk, attached = _world(tmp_path, {NOTE: WRITTEN})
    more = WRITTEN.replace("- Ship on Friday", "- Ship on Friday\n- Ravi owns pricing")
    list(run_harness_turn(attached, "go", _Writes([(attached / NOTE, more)]), extra_mounts=[desk],
                          author=AUTHOR, note_home=desk))
    assert "- Ravi owns pricing" in (desk / NOTE).read_text()
    assert "- Ship on Friday" in (desk / NOTE).read_text()
    assert not (attached / NOTE).exists()
    assert NOTE not in _git(attached, "ls-files")                # the removal is a commit, reversible


def test_the_home_is_committed_even_when_the_turn_does_not_mount_it_for_commit(tmp_path):
    desk, attached = _world(tmp_path)
    list(run_harness_turn(attached, "go", _Writes([(attached / NOTE, WRITTEN)]), author=AUTHOR,
                          note_home=desk))
    assert (desk / NOTE).read_text() == WRITTEN
    assert _git(desk, "status", "--porcelain") == ""


def test_a_written_home_note_keeps_its_own_lines(tmp_path):
    home = WRITTEN + "\nA line Jane typed herself.\n"
    turn = WRITTEN.replace("transcript_cursor: 29", "transcript_cursor: 40").replace(
        "- Ship on Friday", "- Ship on Friday\n- Ravi owns pricing")
    merged = fold(home, WRITTEN, turn)
    assert "A line Jane typed herself." in merged
    assert "- Ravi owns pricing" in merged and "transcript_cursor: 40" in merged


def test_ordinary_pages_and_writes_on_the_home_itself_are_untouched(tmp_path):
    desk, attached = _world(tmp_path)
    other = "kg/entities/meeting/not-on-the-desk.md"
    list(run_harness_turn(attached, "go", _Writes([(attached / other, EMPTY), (desk / NOTE, WRITTEN)]),
                          extra_mounts=[desk], author=AUTHOR, note_home=desk))
    assert (attached / other).is_file() and not (desk / other).exists()
    assert (desk / NOTE).read_text() == WRITTEN


def test_no_home_no_redirect(tmp_path):
    desk, attached = _world(tmp_path)
    (attached / NOTE).parent.mkdir(parents=True, exist_ok=True)
    (attached / NOTE).write_text(WRITTEN)
    assert redirect_stray_notes(None, [attached, desk]) == []
    assert (attached / NOTE).exists()


def test_the_home_is_the_owners_seed_slot_never_an_attached_workspace(tmp_path):
    root = tmp_path / "workspaces"
    assert home_of(OWNER, [root / ".attached" / OWNER / OWNER, root / "team", root / OWNER]) == root / OWNER
    assert home_of(OWNER, [root / ".attached" / OWNER / "biz"]) is None
    assert home_of("", [root / OWNER]) is None


def test_nothing_written_means_no_words():
    assert nothing_written(EMPTY)
    assert nothing_written(EMPTY + "\n## Decisions\n<!-- meeting:decisions:start -->\n\n<!-- meeting:decisions:end -->\n")
    assert not nothing_written(WRITTEN)


def test_the_worker_names_the_owners_writable_seed_slot(monkeypatch):
    from worker.engine import _note_home
    monkeypatch.setenv("VEXA_OWNER", OWNER)
    mounts = [{"path": f"/workspaces/.attached/{OWNER}/biz", "write": True},
              {"path": f"/workspaces/{OWNER}", "write": True},
              {"path": "/workspaces/_global", "write": False}]
    assert _note_home(mounts) == Path(f"/workspaces/{OWNER}")
    assert _note_home([{"path": f"/workspaces/{OWNER}", "write": False}]) is None
