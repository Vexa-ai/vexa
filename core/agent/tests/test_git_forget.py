"""A forget takes the item out of the workspace's HISTORY, not just its tree (Vexa-ai/vexa#1783).

The case from app.dev, 2026-10-13: a person dropped a power of attorney into a chat; the drawer
file `uploads/<hash>-docx` was swept into commit d4218da. "Delete information about that" has to
leave no object any ref can reach. Values here are made up."""
from __future__ import annotations

import subprocess
from pathlib import Path

from control_plane import git_forget as G

UPLOAD = "uploads/7ac36fe6f2952885-docx"
SECRET = "Passport no. AB1234567"


def _git(ws, *a):
    return subprocess.run(["git", "-C", str(ws), *a], capture_output=True, text=True, check=True).stdout


def _repo(tmp_path: Path) -> tuple[Path, str]:
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "config", "user.email", "t@example.test")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("# Desk\n")
    _git(ws, "add", "-A"); _git(ws, "commit", "-q", "-m", "seed")
    (ws / "uploads").mkdir()
    (ws / UPLOAD).write_bytes(b"PK\x03\x04\x00binary power of attorney " + SECRET.encode())
    _git(ws, "add", "-A"); _git(ws, "commit", "-q", "-m", "attach")
    blob = _git(ws, "rev-parse", f"HEAD:{UPLOAD}").strip()
    (ws / "notes.md").write_text(f"# Notes\n\nThe client sent a POA. {SECRET}.\nKeep this line.\n")
    _git(ws, "add", "-A"); _git(ws, "commit", "-q", "-m", "notes")
    return ws, blob


def test_the_upload_and_the_number_are_gone_from_every_commit(tmp_path):
    ws, blob = _repo(tmp_path)
    assert G.contains(ws, paths=[UPLOAD], pattern="AB1234567")
    (ws / "draft.md").write_text("pending, not yet committed\n")
    out = G.forget(ws, paths=[UPLOAD], pattern=None, identifiers=True)
    assert not G.contains(ws, paths=[UPLOAD], pattern="AB1234567")
    assert subprocess.run(["git", "-C", str(ws), "cat-file", "-e", blob]).returncode != 0
    assert out.files_removed == {UPLOAD} and out.commits_changed >= 2 and out.matches_redacted >= 1
    assert "Keep this line." in (ws / "notes.md").read_text()
    assert len(_git(ws, "rev-list", "--all").split()) == 4, "no commit is lost, only its content"
    assert (ws / "draft.md").exists(), "pending work survives the rewrite"


def test_a_text_pattern_alone_is_redacted_from_history_and_messages(tmp_path):
    ws, _ = _repo(tmp_path)
    _git(ws, "commit", "-q", "--allow-empty", "-m", "about Acme Widgets")
    out = G.forget(ws, paths=[], pattern="acme widgets", identifiers=False)
    assert not G.contains(ws, paths=[], pattern="Acme Widgets")
    assert out.matches_redacted == 1


def test_an_untracked_attachment_is_removed_from_disk(tmp_path):
    ws, _ = _repo(tmp_path)
    (ws / "uploads" / "second-docx").write_bytes(b"never committed")
    out = G.forget(ws, paths=["uploads/second-docx"], pattern=None, identifiers=False)
    assert not (ws / "uploads" / "second-docx").exists()
    assert "uploads/second-docx" in out.files_removed


def test_a_link_in_the_workspace_never_carries_the_forget_outside_it(tmp_path):
    """A path through a link the tools planted (`lnk -> ../<victim>`) removes nothing behind the
    link: the victim's folder survives, and a real folder inside the workspace is still removed."""
    ws, _blob = _repo(tmp_path)
    victim = tmp_path / "victim"
    (victim / "notes").mkdir(parents=True)
    (victim / "notes" / "deal.md").write_text("# theirs\n")
    (ws / "lnk").symlink_to(Path("..") / "victim")
    (ws / "drafts").mkdir()
    (ws / "drafts" / "a.md").write_text("draft\n")
    out = G.forget(ws, paths=["lnk/notes", "drafts"], pattern=None, identifiers=False)
    assert (victim / "notes" / "deal.md").read_text() == "# theirs\n"
    assert "lnk/notes" not in out.files_removed
    assert not (ws / "drafts").exists()
