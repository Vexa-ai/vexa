"""Tests for git_credentials — the per-user, save-once reusable GitHub token store.

The store moved from a PLAINTEXT file to the sealed :mod:`control_plane.secret_store` (one server-side
key). These tests hold the two things that must stay true across that move: the round-trip and the mask
are unchanged for every caller, and the clear token is nowhere on disk — including for an account whose
token was written by the OLD code, which is migrated the first time anybody reads it.
"""
import os

from control_plane import git_credentials as gc


def _disk(root) -> bytes:
    """Every byte the secrets root holds — what an attacker with the volume would read."""
    blob = b""
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as fh:
                blob += fh.read()
    return blob


def test_set_read_mask_and_clear(tmp_path):
    root = tmp_path
    assert gc.read_github_token(root, "42") is None
    assert gc.masked_github_token(root, "42") is None

    # save → readable server-side, masked for display (never the clear value)
    assert gc.set_github_token(root, "42", "ghp_ABCDEFGH1234wxyz") is True
    assert gc.read_github_token(root, "42") == "ghp_ABCDEFGH1234wxyz"
    assert gc.masked_github_token(root, "42") == "••••wxyz"

    # stored under a dot-dir the workspace scanners skip, NOT inside a workspace tree — and SEALED
    f = root / ".secrets" / "pat" / "42.enc"
    assert f.exists()
    assert oct(f.stat().st_mode)[-3:] == "600"  # owner-only
    assert b"ghp_ABCDEFGH1234wxyz" not in _disk(root), "the clear token must not exist on disk"

    # per-subject isolation
    assert gc.read_github_token(root, "43") is None

    # clear
    assert gc.set_github_token(root, "42", "") is False
    assert gc.read_github_token(root, "42") is None
    assert gc.masked_github_token(root, "42") is None


def test_a_legacy_plaintext_token_still_reads_and_is_migrated(tmp_path):
    """The upgrade cannot lock anyone out: a token written by the old code is read once more, re-sealed,
    and the plaintext file is removed — so the clear value stops existing without anyone re-entering it."""
    legacy = tmp_path / ".secrets" / "u_old.ghtoken"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("ghp_LEGACYplaintext99")

    assert gc.read_github_token(tmp_path, "u_old") == "ghp_LEGACYplaintext99"
    assert not legacy.exists(), "the plaintext file must be gone after the migrating read"
    assert (tmp_path / ".secrets" / "pat" / "u_old.enc").exists()
    assert gc.read_github_token(tmp_path, "u_old") == "ghp_LEGACYplaintext99"  # still readable, now sealed
    assert b"ghp_LEGACYplaintext99" not in _disk(tmp_path)


def test_saving_removes_any_legacy_plaintext_for_that_subject(tmp_path):
    legacy = tmp_path / ".secrets" / "u_x.ghtoken"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("ghp_OLDVALUE1234")
    gc.set_github_token(tmp_path, "u_x", "ghp_NEWVALUE5678")
    assert not legacy.exists()
    assert gc.read_github_token(tmp_path, "u_x") == "ghp_NEWVALUE5678"


def test_short_token_masks_without_leaking(tmp_path):
    gc.set_github_token(tmp_path, "1", "abcd")  # < 8 chars → mask shows no tail
    assert gc.masked_github_token(tmp_path, "1") == "••••"


def test_invalid_subject_rejected(tmp_path):
    assert gc._name("../escape") is None
    assert gc._name("") is None
    assert gc.read_github_token(tmp_path, "../escape") is None
    try:
        gc.set_github_token(tmp_path, "../escape", "x")
        assert False, "expected ValueError"
    except ValueError:
        pass
