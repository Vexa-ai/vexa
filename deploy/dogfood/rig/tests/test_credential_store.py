"""R-D05 · R-D09 — the credential stores: sealed, owner-only, locked, and atomic."""
from __future__ import annotations

import json
import os
import pathlib
import threading

import rig_secrets
from conftest import STATE
import vexa_control_mcp as rig


def _enc(name: str) -> pathlib.Path:
    return STATE / ".secrets" / f"{name}.enc"


def test_rd05_credential_stores_are_sealed_and_owner_only():
    """GATE 2a (R-D05). A minted token must not appear in any file on disk, and the file that holds
    it must be 0600 in a 0700 directory.

    The symptom this replaces, read off the live host: `-rw-rw-r-- ~/.storm/user-api-keys.json`,
    `oauth/logins.json`, `oauth/email-codes.json` — every user's gateway key, every minted
    `vxa_mcp_` token and every live sign-in code readable by any local account, on a box with four
    other human users, while decision 25 said "encrypted at rest"."""
    rig._token_put("vxa_mcp_SEALEDTOKENSAMPLE", {"uid": "7", "email": "a@b.c"})
    rig_secrets.write("user-api-keys", {"7": "gwkey_SAMPLEVALUE"})

    assert rig._tokens()["vxa_mcp_SEALEDTOKENSAMPLE"]["uid"] == "7"

    leaked = []
    for p in STATE.rglob("*"):
        if p.is_file():
            blob = p.read_bytes()
            if b"vxa_mcp_SEALEDTOKENSAMPLE" in blob or b"gwkey_SAMPLEVALUE" in blob:
                leaked.append(str(p))
    assert leaked == [], f"plaintext credential on disk: {leaked}"

    for name in ("mcp-tokens", "user-api-keys"):
        p = _enc(name)
        assert p.is_file(), f"{name} is not in the encrypted store"
        assert oct(p.stat().st_mode & 0o777) == "0o600"
        assert oct(p.parent.stat().st_mode & 0o777) == "0o700"


def test_rd05_legacy_plaintext_is_migrated_then_removed():
    """GATE 2b (R-D05). The plaintext file an older rig left behind is read once, sealed, verified,
    and only then unlinked — a migration that deletes before it verifies is data loss."""
    legacy = STATE / "legacy-store.json"
    legacy.write_text(json.dumps({"tok_LEGACY": {"uid": "9"}}))
    os.chmod(legacy, 0o664)

    got = rig_secrets.read("legacy-store")

    assert got == {"tok_LEGACY": {"uid": "9"}}, "the migration lost the contents"
    assert not legacy.exists(), "the plaintext file survived the migration"
    assert b"tok_LEGACY" not in _enc("legacy-store").read_bytes()
    assert rig_secrets.read("legacy-store") == {"tok_LEGACY": {"uid": "9"}}


def test_rd09_concurrent_writers_do_not_lose_a_token():
    """GATE 5 (R-D09). Twenty concurrent sign-ins must leave twenty tokens.

    Every credential file was an unlocked read-modify-write with a truncating save, and
    `mcp-tokens.json` had three independent writers. Two sign-ins in the same second lost one
    token; a crash between the read and the write emptied the store and signed everybody out
    permanently. `rig_secrets.update` holds an exclusive flock across both halves and the write
    underneath it is write-temp-and-replace."""
    rig_secrets.write("race-store", {})
    errors = []

    def add(i):
        try:
            rig_secrets.update("race-store", lambda d: d.update({f"tok{i}": {"uid": str(i)}}) or d)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(rig_secrets.read("race-store")) == 20
