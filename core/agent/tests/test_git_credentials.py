"""Tests for git_credentials — the per-user, save-once reusable GitHub token store."""
from control_plane import git_credentials as gc


def test_set_read_mask_and_clear(tmp_path):
    root = tmp_path
    assert gc.read_github_token(root, "42") is None
    assert gc.masked_github_token(root, "42") is None

    # save → readable server-side, masked for display (never the clear value)
    assert gc.set_github_token(root, "42", "ghp_ABCDEFGH1234wxyz") is True
    assert gc.read_github_token(root, "42") == "ghp_ABCDEFGH1234wxyz"
    assert gc.masked_github_token(root, "42") == "••••wxyz"

    # stored under a dot-dir the workspace scanners skip, NOT inside a workspace tree
    f = root / ".secrets" / "42.ghtoken"
    assert f.exists()
    import os
    assert oct(f.stat().st_mode)[-3:] == "600"  # owner-only

    # per-subject isolation
    assert gc.read_github_token(root, "43") is None

    # clear
    assert gc.set_github_token(root, "42", "") is False
    assert gc.read_github_token(root, "42") is None
    assert gc.masked_github_token(root, "42") is None


def test_short_token_masks_without_leaking(tmp_path):
    gc.set_github_token(tmp_path, "1", "abcd")  # < 8 chars → mask shows no tail
    assert gc.masked_github_token(tmp_path, "1") == "••••"


def test_invalid_subject_rejected(tmp_path):
    assert gc._token_path(tmp_path, "../escape") is None
    assert gc._token_path(tmp_path, "") is None
    try:
        gc.set_github_token(tmp_path, "../escape", "x")
        assert False, "expected ValueError"
    except ValueError:
        pass
