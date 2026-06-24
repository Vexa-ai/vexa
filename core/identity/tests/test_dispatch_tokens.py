"""dispatch_tokens — mint → verify roundtrip, tamper/expiry rejection, boundary checks."""
from __future__ import annotations

import pytest

from identity_core.dispatch_tokens import (
    DispatchTokenError,
    WorkspaceGrant,
    mint_dispatch_token,
    verify_dispatch_token,
)

KEY = "dev-signing-key-do-not-ship"
WS = [WorkspaceGrant("u_jane", "rw"), WorkspaceGrant("system", "ro")]


def test_mint_verify_roundtrip_carries_chain_of_custody():
    tok = mint_dispatch_token("u_jane", "user:u_jane", WS, ["gmail.read"], key=KEY, now=1000, ttl_sec=900)
    claims = verify_dispatch_token(tok, key=KEY, now=1100)
    assert claims.subject == "u_jane"
    assert claims.launcher == "user:u_jane"
    assert claims.tools == ("gmail.read",)
    assert claims.iat == 1000 and claims.exp == 1900


def test_mount_boundary_rw_only_where_granted():
    claims = verify_dispatch_token(mint_dispatch_token("u_jane", "user:u_jane", WS, key=KEY, now=1000), key=KEY, now=1000)
    assert claims.may_mount("u_jane", "rw") is True       # granted rw
    assert claims.may_mount("u_jane", "ro") is True       # rw implies ro read
    assert claims.may_mount("system", "ro") is True       # granted ro
    assert claims.may_mount("system", "rw") is False      # NOT granted rw — boundary denies escalation
    assert claims.may_mount("company", "ro") is False     # not granted at all


def test_tool_boundary():
    claims = verify_dispatch_token(mint_dispatch_token("u_jane", "x", WS, ["gmail.read"], key=KEY, now=1000), key=KEY, now=1000)
    assert claims.may_call("gmail.read") is True
    assert claims.may_call("gmail.send") is False


def test_tampered_signature_rejected():
    tok = mint_dispatch_token("u_jane", "user:u_jane", WS, key=KEY, now=1000)
    head, payload, _sig = tok.split(".")
    # forge a token granting rw on `company` then re-sign with the WRONG key
    forged = mint_dispatch_token("u_jane", "user:u_jane", [WorkspaceGrant("company", "rw")], key="attacker", now=1000)
    with pytest.raises(DispatchTokenError) as e:
        verify_dispatch_token(forged, key=KEY, now=1000)
    assert e.value.code == "bad-signature"


def test_expired_rejected():
    tok = mint_dispatch_token("u_jane", "user:u_jane", WS, key=KEY, now=1000, ttl_sec=60)
    with pytest.raises(DispatchTokenError) as e:
        verify_dispatch_token(tok, key=KEY, now=2000)
    assert e.value.code == "expired"


def test_malformed_rejected():
    with pytest.raises(DispatchTokenError) as e:
        verify_dispatch_token("not-a-token", key=KEY)
    assert e.value.code == "malformed"
