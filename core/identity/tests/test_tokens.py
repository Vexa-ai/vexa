"""Scoped-token evals — mint + validate against identity.v1 ScopedToken semantics.

Asserts: in-scope valid → accepted; out-of-scope → rejected; expired → rejected. Derived from
admin-api `/internal/validate` (expired → 401, scope mismatch → 403).
"""

from datetime import datetime, timedelta, timezone

import pytest

from identity_core import ScopedToken, TokenError, mint_token, validate_token

NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)


def test_in_scope_token_is_valid():
    """A non-expired token with the required scope validates and is returned."""
    token = mint_token("42", ["bot", "tx"], expires_at=NOW + timedelta(hours=1), email="o@vexa.ai")
    out = validate_token(token, required_scope="tx", now=NOW)
    assert out is token
    assert out.subject == "42"
    assert out.has_scope("tx")


def test_out_of_scope_token_rejected():
    """A token lacking the required capability is rejected with `missing-scope`."""
    token = mint_token("42", ["bot"], expires_at=NOW + timedelta(hours=1))
    with pytest.raises(TokenError) as ei:
        validate_token(token, required_scope="tx", now=NOW)
    assert ei.value.code == "missing-scope"


def test_expired_token_rejected():
    """A token past its expiry is rejected with `token-expired` (before any scope check)."""
    token = mint_token("42", ["tx"], expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(TokenError) as ei:
        validate_token(token, required_scope="tx", now=NOW)
    assert ei.value.code == "token-expired"


def test_non_expiring_token_never_expires():
    """expires_at=None means the token is accepted at any instant."""
    token = mint_token("7", ["tx"], expires_at=None)
    assert token.is_expired(NOW) is False
    assert validate_token(token, required_scope="tx", now=NOW) is token


def test_mint_rejects_unknown_scope():
    """Minting with a scope outside {bot, tx, browser} is rejected (parent → 422)."""
    with pytest.raises(ValueError):
        mint_token("42", ["admin"])


def test_mint_rejects_empty_scopes():
    with pytest.raises(ValueError):
        mint_token("42", [])


def test_naive_expiry_treated_as_utc():
    """admin-api stores naive UTC expiries; a naive expires_at must compare correctly."""
    naive_expired = ScopedToken("42", ("tx",), expires_at=datetime(2020, 1, 1))
    assert naive_expired.is_expired(NOW) is True


def test_to_contract_shape_matches_identity_v1():
    """to_contract() emits the ScopedToken wire shape with an RFC3339 Z expiry."""
    token = mint_token("42", ["tx"], expires_at=NOW, email="o@vexa.ai")
    blob = token.to_contract()
    assert blob["subject"] == "42"
    assert blob["scopes"] == ["tx"]
    assert blob["expires_at"].endswith("Z")
    assert blob["email"] == "o@vexa.ai"
