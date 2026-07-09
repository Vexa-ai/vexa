"""workspace_purpose — a per-workspace PURPOSE that is stored in the workspace, travels when shared,
and is declared to the agent via the mount preamble.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from control_plane.workspace_purpose import PURPOSE_FILE, read_purpose, write_purpose
from worker.engine import mounts_preamble


def _git_ws(path: Path) -> Path:
    path.mkdir(parents=True)
    for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True, text=True)
    (path / "README.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True, capture_output=True, text=True)
    return path


def test_read_empty_when_unset(tmp_path):
    assert read_purpose(tmp_path) == ""


def test_write_then_read_roundtrip_and_commit(tmp_path):
    ws = _git_ws(tmp_path / "ws")
    stored = write_purpose(ws, "  The ACME  deal room —\n keep pricing here.  ")
    # normalized to a single trimmed line
    assert stored == "The ACME deal room — keep pricing here."
    assert read_purpose(ws) == stored
    assert (ws / PURPOSE_FILE).exists()
    # committed to the workspace's own history (so it travels when shared)
    log = subprocess.run(["git", "-C", str(ws), "log", "--oneline", "--", PURPOSE_FILE],
                         capture_output=True, text=True).stdout
    assert "purpose" in log.lower()


def test_clearing_removes_the_file(tmp_path):
    ws = _git_ws(tmp_path / "ws")
    write_purpose(ws, "temporary")
    assert (ws / PURPOSE_FILE).exists()
    assert write_purpose(ws, "") == ""
    assert not (ws / PURPOSE_FILE).exists()
    assert read_purpose(ws) == ""


def test_write_on_non_git_dir_still_persists(tmp_path):
    ws = tmp_path / "plain"
    ws.mkdir()
    assert write_purpose(ws, "no git here") == "no git here"
    assert read_purpose(ws) == "no git here"


def test_purpose_renders_in_the_mount_preamble(tmp_path):
    mounts = [
        {"slug": "personal", "path": "/w/p", "role": "private", "write": True, "primary": True, "purpose": ""},
        {"slug": "acme-deal", "path": "/w/acme", "role": "shared", "write": True, "primary": False,
         "purpose": "The ACME deal room — pricing + contacts."},
    ]
    text = mounts_preamble(mounts)
    assert "Purpose: The ACME deal room — pricing + contacts." in text
    # a mount with no purpose contributes no Purpose line
    assert text.count("Purpose:") == 1
