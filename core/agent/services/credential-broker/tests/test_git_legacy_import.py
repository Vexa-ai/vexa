"""The one-shot import moves Git credentials agent-api kept on its own volume into this store.

What it must hold: every entry the old key opens lands under `git/<name>` and is served by the git
route; the old file goes only after the value was read back; a name the broker already holds — a
tombstone included — is never overwritten; an entry the key cannot open is kept and counted; a
second run does nothing; and nothing printed carries a name or a value.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from pathlib import Path

from credential_broker import import_git_legacy as imp
from credential_broker.store import LocalEncryptedStore


def _seal(value: str, key: bytes) -> str:
    """The retired agent-api store's envelope format — test fixture only."""
    salt = secrets.token_bytes(16)
    enc = hmac.new(key, b"vexa.secret.enc/" + salt, hashlib.sha256).digest()
    mac = hmac.new(key, b"vexa.secret.mac/" + salt, hashlib.sha256).digest()
    stream, n = bytearray(), 0
    while len(stream) < len(value.encode()):
        stream += hashlib.sha256(enc + n.to_bytes(8, "big")).digest()
        n += 1
    ct = bytes(a ^ b for a, b in zip(value.encode(), stream))
    tag = hmac.new(mac, salt + ct, hashlib.sha256).digest()
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    return ".".join(("v1", b64(salt), b64(ct), b64(tag)))


def _legacy(root: Path, entries: dict, *, operator_key: str = "") -> Path:
    d = root / ".secrets"
    d.mkdir(parents=True)
    if operator_key:
        key = hashlib.sha256(operator_key.encode()).digest()
    else:
        (d / ".master.key").write_bytes(base64.b64encode(secrets.token_bytes(32)))
        key = hashlib.sha256((d / ".master.key").read_bytes().strip()).digest()
    for name, value in entries.items():
        p = d / f"{name}.enc"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_seal(value, key))
    return d


def test_everything_moves_is_read_back_and_the_old_store_is_gone(tmp_path, store):
    d = _legacy(tmp_path, {"pat/2": "fixture-token", "deploy/ws-team.priv": "fixture-private",
                           "deploy/ws-team.pub": "fixture-public"})
    (d / "3.ghtoken").write_text("fixture-plaintext")
    counts = imp.run(d, store)
    assert counts == {"moved": 4, "already_held": 0, "unreadable": 0, "invalid": 0, "failed": 0}
    assert store.get("git/pat/2").data == {"value": "fixture-token"}
    assert store.get("git/pat/3").data == {"value": "fixture-plaintext"}
    assert store.get("git/deploy/ws-team.priv").data == {"value": "fixture-private"}
    assert not d.exists(), "the old ciphertext and its generated key are removed once all moved"
    assert imp.run(d, store) == {"moved": 0, "already_held": 0, "unreadable": 0, "invalid": 0, "failed": 0}


def test_the_operator_key_opens_what_it_sealed(tmp_path, store):
    d = _legacy(tmp_path, {"pat/2": "fixture-token"}, operator_key="fixture-operator-key")
    assert imp.run(d, store, operator_key="fixture-operator-key\n")["moved"] == 1
    assert store.get("git/pat/2").data == {"value": "fixture-token"}


def test_a_held_name_is_never_overwritten_and_a_tombstone_is_not_resurrected(tmp_path, store):
    store.put("git/pat/2", {"value": None})            # revoked through the broker
    store.put("git/pat/3", {"value": "newer"})
    d = _legacy(tmp_path, {"pat/2": "old", "pat/3": "older"})
    counts = imp.run(d, store)
    assert counts["already_held"] == 2 and counts["moved"] == 0
    assert store.get("git/pat/2").data == {"value": None}
    assert store.get("git/pat/3").data == {"value": "newer"}
    assert not d.exists()


def test_an_entry_the_key_cannot_open_is_kept(tmp_path, store):
    d = _legacy(tmp_path, {"pat/2": "fixture-token"}, operator_key="fixture-operator-key")
    counts = imp.run(d, store, operator_key="a-different-key")
    assert counts["unreadable"] == 1 and store.get("git/pat/2") is None
    assert (d / "pat" / "2.enc").exists()


def test_a_store_failure_removes_nothing(tmp_path, store):
    d = _legacy(tmp_path, {"pat/2": "fixture-token"})
    store.fail = "transport"
    assert imp.run(d, store)["failed"] == 1
    assert (d / "pat" / "2.enc").exists() and (d / ".master.key").exists()


def test_against_the_real_encrypted_store(tmp_path):
    real = LocalEncryptedStore(tmp_path / "secrets.sqlite", secrets.token_bytes(32))
    d = _legacy(tmp_path / "legacy", {"deploy/user-2.priv": "fixture-private"})
    assert imp.run(d, real)["moved"] == 1
    assert real.get("git/deploy/user-2.priv").data == {"value": "fixture-private"}


def test_the_git_route_serves_what_was_imported(tmp_path, store, signed):
    d = _legacy(tmp_path, {"pat/2": "fixture-token"})
    imp.run(d, store)
    r = signed("git", "POST", "/api/internal/git-secret", {"name": "pat/2", "action": "get", "value": None}, actor="2")
    assert r.json() == {"found": True, "value": "fixture-token"}


def test_the_command_prints_counts_only(tmp_path, store, monkeypatch, capsys):
    d = _legacy(tmp_path, {"pat/2": "fixture-token"})
    monkeypatch.setattr("credential_broker.main.build_store", lambda settings: store)
    monkeypatch.setenv("VEXA_CONNECTIONS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VEXA_CONNECTIONS_STORE_KEY_FILE", str(tmp_path / "unused"))
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", str(tmp_path / "unused"))
    (tmp_path / "state").mkdir()
    assert imp.main([str(d)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"event": "git_legacy_import", "moved": 1, "already_held": 0,
                               "unreadable": 0, "invalid": 0, "failed": 0}
    assert "fixture" not in out and "pat/2" not in out
