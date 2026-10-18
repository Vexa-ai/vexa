"""Finding 1 (live, 2026-09-03) — the seal migration was LAZY, so a restart sealed almost nothing.

`rig_secrets.read()` migrates a legacy plaintext store on FIRST READ. That is correct per store and
wrong for the deployment: after the restart that shipped the sealed store, only `mcp-tokens` had
been read, so only `mcp-tokens` was sealed. `user-api-keys.json` — every user's gateway API key —
sat in plaintext until something happened to ask for it, which is a window measured in whatever the
traffic happens to be. The migration ran; the operator had no way to know it had not finished.

So the migration is EAGER: every known store at process start, and a registry that a new store
cannot dodge.
"""
from __future__ import annotations

import json
import pathlib
import tempfile

import rig_secrets


LEGACY = {
    "mcp-tokens": {"vxa_mcp_AAA": {"uid": "1", "email": "a@b.c"}},
    "user-api-keys": {"1": "gwkey_LIVEVALUE"},
    "oauth/logins": {"code1": {"uid": "1", "email": "a@b.c", "exp": 9999999999}},
    "oauth/email-codes": {"a@b.c": {"code": "123456", "exp": 9999999999, "tries": 0}},
    "oauth/regimes": {"1": {"mode": "cloud"}},
    "oauth/clients": {"cid": {"client_name": "someone"}},
    "oauth/codes": {"c": {"cid": "cid"}},
    "oauth/tokens": {"tok_LIVEBEARER": {"uid": "1", "exp": 9999999999}},
}


def _legacy_dir() -> pathlib.Path:
    """A directory of plaintext stores exactly as an older rig left them: 0664, nothing sealed."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="rig-legacy-"))
    for name, data in LEGACY.items():
        p = d / f"{name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=1))
        p.chmod(0o664)
    return d


def test_every_known_store_is_registered():
    """The registry is the fix. A lazy migration is only half the defect — the other half is that
    nothing anywhere listed the stores, so nobody could ask "did it finish?"."""
    assert set(rig_secrets.STORES) >= set(LEGACY), \
        f"a store is missing from the registry: {set(LEGACY) - set(rig_secrets.STORES)}"


def test_process_start_leaves_no_plaintext_store_behind():
    """GATE (finding 1). Start against a directory of legacy stores; none may remain."""
    d = _legacy_dir()
    original, rig_secrets.STATE_DIR = rig_secrets.STATE_DIR, d
    try:
        report = rig_secrets.migrate_all()

        left = sorted(str(p.relative_to(d)) for p in d.rglob("*.json") if p.is_file())
        assert left == [], f"plaintext stores survived process start: {left}"
        assert sorted(report["sealed"]) == sorted(LEGACY), report

        # …and the values came through, sealed, readable, owner-only.
        for name, data in LEGACY.items():
            assert rig_secrets.read(name) == data, name
            enc = d / ".secrets" / f"{name}.enc"
            assert enc.is_file() and oct(enc.stat().st_mode & 0o777) == "0o600"
            assert b"gwkey_LIVEVALUE" not in enc.read_bytes()
            assert b"tok_LIVEBEARER" not in enc.read_bytes()
    finally:
        rig_secrets.STATE_DIR = original


def test_migration_is_idempotent_and_survives_a_second_start():
    d = _legacy_dir()
    original, rig_secrets.STATE_DIR = rig_secrets.STATE_DIR, d
    try:
        rig_secrets.migrate_all()
        again = rig_secrets.migrate_all()
        assert again["sealed"] == [], "a second start re-sealed something already sealed"
        assert rig_secrets.read("user-api-keys") == LEGACY["user-api-keys"]
    finally:
        rig_secrets.STATE_DIR = original


def test_a_plaintext_file_this_module_does_not_own_is_reported_not_ignored():
    """`witness-data.json` was in the live finding and is NOT a store this module owns. Silence
    about it would repeat the original mistake one level up: the operator reads "migration done"
    and cannot tell that something plaintext is still sitting there."""
    d = _legacy_dir()
    (d / "witness-data.json").write_text('{"seen": 1}')
    original, rig_secrets.STATE_DIR = rig_secrets.STATE_DIR, d
    try:
        report = rig_secrets.migrate_all()
        assert "witness-data.json" in report["not_ours"], report
        assert (d / "witness-data.json").is_file(), "it is not ours to delete"
    finally:
        rig_secrets.STATE_DIR = original


def test_no_store_name_in_the_rig_escapes_the_registry():
    """The registry is only a fix while it is TOTAL. Read the store-name constants out of the two
    rig modules and require each one's value to be registered — otherwise the next store added
    migrates late and silently, which is the defect this file exists to close.

    (`signing_key` mints `keys/<name>` on demand; those are born sealed by `update` and never have
    a plaintext ancestor, so they are not migration candidates and are not registered.)
    """
    import ast

    rig_dir = pathlib.Path(__file__).resolve().parents[1]
    declared = {
        "vexa_control_mcp.py": ("TOKENS_STORE", "EMAIL_CODES_STORE", "LOGINS_STORE",
                                "REGIMES_STORE", "USER_KEYS_STORE"),
        "vexa_oauth.py": ("CLIENTS", "CODES", "TOKENS"),
    }
    seen = set()
    for fname, names in declared.items():
        tree = ast.parse((rig_dir / fname).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    assert isinstance(node.value, ast.Constant), f"{fname}:{t.id} is not a name"
                    seen.add(node.value.value)
                    assert node.value.value in rig_secrets.STORES, (
                        f"{fname}:{t.id} = {node.value.value!r} is not in rig_secrets.STORES — "
                        "an unregistered store migrates late and silently")
    assert seen == set(rig_secrets.STORES), \
        f"registry and callers disagree: only-in-registry={set(rig_secrets.STORES) - seen}"
