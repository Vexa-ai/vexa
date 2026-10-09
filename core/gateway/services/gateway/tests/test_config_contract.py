"""#526 · config.v1 (ADR-0026) — gateway's declaration + boot preflight.

The gateway attaches INTERNAL_API_SECRET as X-Internal-Secret on the admin-api /internal/validate
hop; unset, admin-api rejects validation and EVERY API-key check 503s — yet the gateway was outside
the config-contract machinery, so it booted green. This pins the declaration + the boot preflight
that refuses a secretless deploy. Offline, stdlib only.
"""
from __future__ import annotations

import pytest

from gateway import config_preflight as cp


def test_declaration_loads_and_internal_secret_is_required():
    decl = cp.load_declaration()
    assert decl["service"] == "gateway"
    required = {k["key"] for k in decl["keys"] if k["class"] == "required-explicit"}
    assert "INTERNAL_API_SECRET" in required


def test_preflight_refuses_boot_without_internal_api_secret():
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight({})
    assert "INTERNAL_API_SECRET" in str(ei.value)


REQUIRED = {"INTERNAL_API_SECRET": "a-real-secret",
            "VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE": "/run/vexa-identity/signing/key.pem"}


def test_preflight_passes_when_required_set():
    cp.preflight(dict(REQUIRED))


def test_preflight_refuses_boot_without_the_identity_signing_key():
    """gateway-identity.v1 — every forward is signed; with no key nothing behind the edge can authenticate a
    request, so the gateway refuses to boot rather than forward identities nobody will believe."""
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight({"INTERNAL_API_SECRET": "a-real-secret"})
    assert "VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE" in str(ei.value)


def test_preflight_refuses_the_published_placeholder():
    """F95 — the failure a required-explicit key does NOT catch.

    `INTERNAL_API_SECRET` was never unset on a stock deploy: compose supplied
    `${INTERNAL_API_SECRET:-vexa-internal-secret}`, a literal in a public repository and the exact
    value the internal tier compared against. The boot was green, the preflight was satisfied, and
    the internal tier was open to anyone who had read the source. A set placeholder is not a
    configured deployment, so it refuses the same way an unset one does — and the message names the
    KEY, never the value."""
    for placeholder in ("vexa-internal-secret", "lite-internal-secret", "changeme"):
        with pytest.raises(cp.ConfigError) as ei:
            cp.preflight({**REQUIRED, "INTERNAL_API_SECRET": placeholder})
        assert "INTERNAL_API_SECRET" in str(ei.value)
        assert placeholder not in str(ei.value), "a refusal must never echo the value"


def test_the_boot_refuses_a_signing_key_file_that_is_not_the_private_key(tmp_path):
    """gateway-identity.v1 — the path being set is not enough: an unreadable file, the PUBLIC key mounted by
    mistake, or an old shared HMAC secret left in place each refuse the boot, and the refusal names
    the key's name, never its material."""
    from gateway import identity_token
    from gateway.adapters import load_signing_key

    key = identity_token.generate_signing_key()
    good = tmp_path / "signing.pem"
    good.write_bytes(identity_token.private_key_pem(key))
    assert load_signing_key(str(good)).public_key() == key.public_key()

    public = tmp_path / "public.pem"
    public.write_bytes(identity_token.public_key_pem(key))
    legacy = tmp_path / "hmac-secret"
    legacy.write_text("4f" * 32)
    for path in ("", str(tmp_path / "missing.pem"), str(public), str(legacy)):
        with pytest.raises(cp.ConfigError) as ei:
            load_signing_key(path)
        assert "VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE" in str(ei.value)
        assert "BEGIN" not in str(ei.value) and "4f4f" not in str(ei.value)


def test_the_boot_refuses_the_published_rfc8032_test_key(tmp_path):
    """gateway-identity.v1 — the contract's signing vectors are made with RFC 8032 TEST 1, whose seed
    is printed in the RFC. A gateway configured with it signs identities anybody can forge, so the
    boot refuses it like a missing key, naming the vector and never the key."""
    from gateway import identity_token
    from gateway.adapters import load_signing_key
    from rfc8032 import rfc8032_test1_signing_key

    published = tmp_path / "signing.pem"
    published.write_bytes(identity_token.private_key_pem(rfc8032_test1_signing_key()))
    with pytest.raises(cp.ConfigError) as ei:
        load_signing_key(str(published))
    assert "VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE" in str(ei.value)
    assert "RFC 8032" in str(ei.value) and "BEGIN" not in str(ei.value)
