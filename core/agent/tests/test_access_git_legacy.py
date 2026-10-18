"""Access-gate deny vectors for agent-api's read-only legacy Git store (Vexa-ai/vexa#1783).

Pure unit tests, no network. The legacy reader opens only regular files that sit inside
``<root>/.secrets`` itself: a symbolic link (to a file, a key, or a directory) reads as nothing and
is never handed to the broker. And once the broker holds a name, the old copy is removed.
"""
from __future__ import annotations

import hashlib

import pytest

from control_plane import git_secret_store as store, legacy_git_store as local
from tests import legacy_git_store_fixture as fixture


@pytest.fixture
def remote(git_broker):
    return git_broker


def _outside(tmp_path, name, text):
    d = tmp_path / "outside"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(text)
    return p


def test_a_linked_plaintext_token_reads_as_nothing_and_is_not_moved(tmp_path, remote):
    root = tmp_path / "ws"
    (root / ".secrets").mkdir(parents=True)
    target = _outside(tmp_path, "key", "outside-value")
    (root / ".secrets" / "2.ghtoken").symlink_to(target)
    assert local.get(root, "pat/2") is None
    assert store.get(root, "pat/2") is None
    assert "pat/2" not in remote
    assert target.read_text() == "outside-value"


def test_a_linked_sealed_entry_reads_as_nothing(tmp_path, remote):
    root = tmp_path / "ws"
    fixture.put(root, "pat/9", "fixture-token")             # creates the dir and a real key
    key = hashlib.sha256((root / ".secrets" / ".master.key").read_bytes().strip()).digest()
    target = _outside(tmp_path, "sealed", fixture.seal("outside-value", key))
    (root / ".secrets" / "pat").mkdir(exist_ok=True)
    (root / ".secrets" / "pat" / "2.enc").symlink_to(target)
    assert local.get(root, "pat/2") is None
    assert store.get(root, "pat/2") is None
    assert "pat/2" not in remote


def test_a_linked_master_key_is_not_read(tmp_path, monkeypatch):
    monkeypatch.delenv("VEXA_SECRETS_KEY", raising=False)
    root = tmp_path / "ws"
    (root / ".secrets").mkdir(parents=True)
    target = _outside(tmp_path, "master", "outside-key-material")
    (root / ".secrets" / ".master.key").symlink_to(target)
    assert local.read_key(root) is None


def test_a_linked_subdirectory_is_not_followed(tmp_path, remote):
    root = tmp_path / "ws"
    fixture.put(root, "pat/9", "fixture-token")             # creates the dir and a real key
    key = hashlib.sha256((root / ".secrets" / ".master.key").read_bytes().strip()).digest()
    _outside(tmp_path, "ws-x.priv.enc", fixture.seal("outside-value", key))
    (root / ".secrets" / "deploy").symlink_to(tmp_path / "outside", target_is_directory=True)
    assert local.get(root, "deploy/ws-x.priv") is None
    assert store.get(root, "deploy/ws-x.priv") is None and "deploy/ws-x.priv" not in remote
    assert all("deploy" not in p.parts for p in local.pending(root))


def test_a_linked_secrets_directory_is_not_read(tmp_path, remote):
    root = tmp_path / "ws"
    root.mkdir()
    _outside(tmp_path, "2.ghtoken", "outside-value")
    (root / ".secrets").symlink_to(tmp_path / "outside", target_is_directory=True)
    assert local.get(root, "pat/2") is None
    assert local.pending(root) == []
    assert store.get(root, "pat/2") is None and "pat/2" not in remote


def test_the_old_copy_is_removed_when_the_broker_already_holds_the_name(tmp_path, remote):
    fixture.put(tmp_path, "pat/2", "old-token")
    (tmp_path / ".secrets" / "2.ghtoken").write_text("older-plaintext")
    remote["pat/2"] = "current-token"
    assert store.get(tmp_path, "pat/2") == "current-token"
    assert local.state(tmp_path, "pat/2") == local.ABSENT
    assert not (tmp_path / ".secrets").exists(), "the sealed copy and its generated key are gone"


def test_the_old_copy_is_removed_when_the_broker_holds_a_tombstone(tmp_path, remote):
    fixture.put(tmp_path, "pat/2", "old-token")
    remote["pat/2"] = None
    assert store.get(tmp_path, "pat/2") is None
    assert not (tmp_path / ".secrets").exists()
