"""shared.host_claude — WHERE the claude subscription credential is read from, and the inode
regression that made the answer wrong for 32h (dogfood, 2026-08-31 → 09-01).

The defect was not "the file was missing". The file was there and correct on the host the whole
time. Compose delivered it as a single-FILE bind, which is resolved once and pinned to that INODE;
the claude CLI refreshes an expiring token by rename(2)-ing a NEW inode over .credentials.json, so
the two long-lived containers kept reading the pre-refresh token until they were recreated. These
tests hold both halves: resolution order (directory mount wins) and freshness across a replace.
"""
from __future__ import annotations

import json

import pytest

from shared import host_claude as hc


@pytest.fixture
def mounts(tmp_path, monkeypatch):
    """A fake pair of mounts: the DIRECTORY one and the LEGACY file one, independently writable."""
    d = tmp_path / "host-claude"
    d.mkdir()
    legacy = tmp_path / "host-claude-credentials"
    monkeypatch.setattr(hc, "CREDENTIALS_DIR_MOUNT", str(d))
    monkeypatch.setattr(hc, "LEGACY_CREDENTIALS_MOUNT", str(legacy))
    return d, legacy


def _write(p, marker):
    p.write_text(json.dumps({"claudeAiOauth": {"accessToken": marker}}))


def test_directory_mount_wins_over_the_legacy_file(mounts):
    d, legacy = mounts
    _write(d / hc.CREDENTIALS_FILENAME, "fresh")
    _write(legacy, "stale")
    assert hc.credentials_path() == str(d / hc.CREDENTIALS_FILENAME)


def test_legacy_file_still_works_when_no_directory_is_mounted(mounts):
    _, legacy = mounts
    _write(legacy, "only-one-there")
    assert hc.credentials_path() == str(legacy)


def test_falls_back_to_the_legacy_path_when_nothing_is_mounted(mounts):
    _, legacy = mounts
    # Neither exists: return the LEGACY path so the caller's own "no credentials mounted" message
    # (which names HOST_CLAUDE_CREDENTIALS and the remedy) is what the operator sees.
    assert hc.credentials_path() == str(legacy)


def test_a_dev_null_directory_mount_is_not_mistaken_for_a_credential(mounts, tmp_path, monkeypatch):
    # compose binds /dev/null at the directory path when HOST_CLAUDE_DIR is unset, so a lookup
    # INSIDE it hits ENOTDIR. That must degrade to the legacy mount, not raise.
    _, legacy = mounts
    notadir = tmp_path / "notadir"
    notadir.write_text("")
    monkeypatch.setattr(hc, "CREDENTIALS_DIR_MOUNT", str(notadir))
    _write(legacy, "the-real-one")
    assert hc.credentials_path() == str(legacy)


def test_resolution_survives_an_inode_swap_the_whole_point(mounts):
    """THE REGRESSION. Replace the file the way the CLI does — write a temp, rename over — and the
    resolver must hand back content from the NEW inode, not a path bound to the old one."""
    d, _ = mounts
    target = d / hc.CREDENTIALS_FILENAME
    _write(target, "before-refresh")
    assert json.loads(open(hc.credentials_path()).read())["claudeAiOauth"]["accessToken"] == "before-refresh"

    tmp = d / ".credentials.json.tmp"
    _write(tmp, "after-refresh")
    tmp.rename(target)          # exactly what an OAuth refresh does: atomic replace, NEW inode

    assert json.loads(open(hc.credentials_path()).read())["claudeAiOauth"]["accessToken"] == "after-refresh"


def test_candidate_order_is_directory_then_legacy():
    # order is load-bearing: the legacy mirror may be a stale inode, so it is always LAST
    assert hc.candidate_paths() == [
        f"{hc.CREDENTIALS_DIR_MOUNT}/{hc.CREDENTIALS_FILENAME}",
        hc.LEGACY_CREDENTIALS_MOUNT,
    ]
