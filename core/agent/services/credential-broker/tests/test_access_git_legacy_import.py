"""Access-gate deny vectors for the one-shot Git import (Vexa-ai/vexa#1783).

Pure unit tests, no network. The import reads only regular files inside the directory it was given:
a symbolic link (to an entry, the generated key, a subdirectory, or the directory itself) is not
read and not imported. Handing the broker's state back to its own user never follows a link.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets

from credential_broker import import_git_legacy as imp

from test_git_legacy_import import _seal

ZERO = {"moved": 0, "already_held": 0, "unreadable": 0, "invalid": 0, "failed": 0}


def _dir_with_key(tmp_path):
    d = tmp_path / "legacy" / ".secrets"
    d.mkdir(parents=True)
    (d / ".master.key").write_bytes(base64.b64encode(secrets.token_bytes(32)))
    return d, hashlib.sha256((d / ".master.key").read_bytes().strip()).digest()


def _outside(tmp_path):
    o = tmp_path / "state"
    o.mkdir(exist_ok=True)
    return o


def test_a_linked_token_file_is_not_imported(tmp_path, store):
    d, _ = _dir_with_key(tmp_path)
    target = _outside(tmp_path) / "broker.key"
    target.write_text("outside-value")
    (d / "3.ghtoken").symlink_to(target)
    assert imp.run(d, store) == ZERO
    assert store.get("git/pat/3") is None
    assert target.read_text() == "outside-value"


def test_a_linked_sealed_entry_is_not_imported(tmp_path, store):
    d, key = _dir_with_key(tmp_path)
    target = _outside(tmp_path) / "sealed"
    target.write_text(_seal("outside-value", key))
    (d / "pat").mkdir()
    (d / "pat" / "2.enc").symlink_to(target)
    assert imp.run(d, store)["moved"] == 0
    assert store.get("git/pat/2") is None


def test_a_linked_subdirectory_is_not_followed(tmp_path, store):
    d, key = _dir_with_key(tmp_path)
    o = _outside(tmp_path)
    (o / "ws-x.priv.enc").write_text(_seal("outside-value", key))
    (d / "deploy").symlink_to(o, target_is_directory=True)
    assert imp.run(d, store)["moved"] == 0
    assert store.get("git/deploy/ws-x.priv") is None
    assert (o / "ws-x.priv.enc").exists()


def test_a_linked_master_key_is_not_read(tmp_path, store):
    d = tmp_path / "legacy" / ".secrets"
    d.mkdir(parents=True)
    target = _outside(tmp_path) / "master"
    target.write_bytes(base64.b64encode(secrets.token_bytes(32)))
    (d / ".master.key").symlink_to(target)
    assert imp.legacy_key(d) is None


def test_a_linked_secrets_directory_is_not_read(tmp_path, store):
    o = _outside(tmp_path)
    (o / "3.ghtoken").write_text("outside-value")
    (tmp_path / "legacy").mkdir()
    link = tmp_path / "legacy" / ".secrets"
    link.symlink_to(o, target_is_directory=True)
    assert imp.run(link, store) == ZERO
    assert store.get("git/pat/3") is None
    assert (o / "3.ghtoken").read_text() == "outside-value"


def test_handing_state_back_never_follows_a_link(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "metadata.sqlite").write_text("")
    (state / "elsewhere").symlink_to(tmp_path / "elsewhere-target")
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda p, u, g, **kw: calls.append((str(p), kw)))
    imp._hand_back(state)
    assert calls, "the state tree is handed back"
    assert all(kw.get("follow_symlinks") is False for _, kw in calls)
