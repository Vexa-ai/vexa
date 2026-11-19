"""The per-turn commit keeps the attachment drawer and identifiers out of history (Vexa-ai/vexa#1783).

app.dev, 2026-10-13: a power of attorney dropped into a chat landed in `uploads/` and the turn's
commit (d4218da) swept it into the workspace's history while the agent said it had saved nothing.
Values here are made up."""
from __future__ import annotations

import subprocess
from pathlib import Path

from llm import ports


def _git(ws, *a):
    return subprocess.run(["git", "-C", str(ws), *a], capture_output=True, text=True, check=True).stdout


def _repo(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-q", "-b", "main")
    (ws / "README.md").write_text("# Desk\n")
    _git(ws, "-c", "user.email=t@example.test", "-c", "user.name=t", "add", "-A")
    _git(ws, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "seed")
    return ws


def test_an_upload_alone_commits_nothing(tmp_path):
    ws = _repo(tmp_path)
    (ws / "uploads").mkdir()
    (ws / "uploads" / "7ac36fe6f2952885-docx").write_bytes(b"PK\x03\x04 power of attorney")
    head = _git(ws, "rev-parse", "HEAD")
    assert ports._commit_mount(ws, message="I haven't saved anything.", author=("Ada", "ada@example.test")) is None
    assert _git(ws, "rev-parse", "HEAD") == head
    assert (ws / "uploads" / "7ac36fe6f2952885-docx").exists(), "the turn still reads it"


def test_a_page_beside_an_upload_commits_the_page_only(tmp_path):
    ws = _repo(tmp_path)
    (ws / "uploads").mkdir()
    (ws / "uploads" / "a-docx").write_bytes(b"PK")
    (ws / "notes.md").write_text("# Notes\n")
    assert ports._commit_mount(ws, message="Noted.", author=("Ada", "ada@example.test"))
    assert _git(ws, "show", "--name-only", "--format=", "HEAD").split() == ["notes.md"]


def test_identifiers_are_removed_before_commit_and_the_body_says_which_kinds(tmp_path):
    ws = _repo(tmp_path)
    (ws / "poa.md").write_text("# POA\n\nPassport no. AB1234567 — signed.\n")
    assert ports._commit_mount(ws, message="Saved the POA summary.", author=("Ada", "ada@example.test"))
    assert "AB1234567" not in _git(ws, "show", "HEAD:poa.md")
    assert "AB1234567" not in (ws / "poa.md").read_text()
    body = _git(ws, "log", "-1", "--format=%B")
    assert "government identifier" in body and "AB1234567" not in body
