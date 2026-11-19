"""Tests for git_credentials — the per-user, save-once reusable GitHub token.

The token lives in the credential broker (ADR-0040). These tests hold what every caller relies on —
the round trip and the mask — and that agent-api keeps no copy on disk, including for an account
whose token an older build wrote locally, which moves into the broker the first time it is read.
"""
import os

import pytest

from control_plane import git_credentials as gc


@pytest.fixture(autouse=True)
def _broker(git_broker):
    return git_broker


def _disk(root) -> bytes:
    """Every byte the workspaces root holds — what an attacker with the volume would read."""
    blob = b""
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as fh:
                blob += fh.read()
    return blob


def test_set_read_mask_and_clear(tmp_path, git_broker):
    root = tmp_path
    assert gc.read_github_token(root, "42") is None
    assert gc.masked_github_token(root, "42") is None

    assert gc.set_github_token(root, "42", "ghp_ABCDEFGH1234wxyz") is True
    assert gc.read_github_token(root, "42") == "ghp_ABCDEFGH1234wxyz"
    assert gc.masked_github_token(root, "42") == "••••wxyz"
    assert git_broker["pat/42"] == "ghp_ABCDEFGH1234wxyz"
    assert _disk(root) == b"", "agent-api must hold no token at rest"

    assert gc.read_github_token(root, "43") is None   # per-subject isolation

    assert gc.set_github_token(root, "42", "") is False
    assert gc.read_github_token(root, "42") is None
    assert gc.masked_github_token(root, "42") is None


def test_a_legacy_plaintext_token_moves_into_the_broker(tmp_path, git_broker):
    legacy = tmp_path / ".secrets" / "u_old.ghtoken"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("ghp_LEGACYplaintext99")

    assert gc.read_github_token(tmp_path, "u_old") == "ghp_LEGACYplaintext99"
    assert not legacy.exists(), "the plaintext file must be gone once the broker confirmed it"
    assert git_broker["pat/u_old"] == "ghp_LEGACYplaintext99"
    assert gc.read_github_token(tmp_path, "u_old") == "ghp_LEGACYplaintext99"
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
    with pytest.raises(ValueError):
        gc.set_github_token(tmp_path, "../escape", "x")
