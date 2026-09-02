"""ONE DELEGATION VERIFIER, and a test that says so.

`core/agent/shared/delegation.py` mints the `vxd_` token agent-api hands a worker; this package
verifies it. The rig verified it with a HAND-ROLLED second implementation — its own `hmac.new`, its
own prefix, its own audience constant, its own denylist — while the library's `verify_delegation`
had no non-test caller anywhere in `core/` (seam inventory B5, row 1). Two HMAC verifiers on one
security surface, in two images, with no test comparing them: the weaker one survives, and nobody
finds out from a failing build.

The module here is that library, VERBATIM, vendored the way `config_preflight.py` is vendored and
checked the same way — byte identity, asserted. It is a copy because the two live in different
images and this repo has no shared Python distribution for them; it is not a REWRITE, and this test
is the difference.
"""
from __future__ import annotations

import pathlib

import pytest

from vexa_mcp import delegation

ROOT = pathlib.Path(__file__).resolve().parents[3]
CANONICAL = ROOT / "core" / "agent" / "shared" / "delegation.py"
VENDORED = pathlib.Path(delegation.__file__)


def test_the_vendored_verifier_is_the_library_byte_for_byte():
    assert CANONICAL.is_file(), f"the canonical delegation module is missing at {CANONICAL}"
    assert VENDORED.read_bytes() == CANONICAL.read_bytes(), (
        "core/mcp's delegation.py has drifted from core/agent/shared/delegation.py. Vendor it "
        "VERBATIM — a second spelling of an HMAC verifier is the failure this test exists to stop.")


def test_a_token_minted_by_the_library_verifies_here():
    secret = "s" * 32
    tok = delegation.mint_delegation(secret, subject="57", regime="human")
    claims = delegation.verify_delegation(secret, tok)
    assert claims["sub"] == "57"
    assert claims["aud"] == delegation.AUDIENCE


def test_an_unset_secret_refuses_everyone():
    """A zero-length HMAC key verifies for anyone who knows the format, so 'delegation is not
    configured here' must mean nobody gets in, never everybody."""
    with pytest.raises(ValueError):
        delegation.verify_delegation("", "vxd_a.b.c")


def test_a_forged_signature_is_refused_before_any_claim_is_read():
    secret = "s" * 32
    tok = delegation.mint_delegation(secret, subject="57", regime="human")
    with pytest.raises(delegation.BadSignature):
        delegation.verify_delegation("t" * 32, tok)
