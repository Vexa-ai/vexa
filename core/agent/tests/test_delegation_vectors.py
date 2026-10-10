"""delegation.v1's vectors, minted and verified by the Python module agent-api and admin-api vendor.

The contract's `validate.mjs` re-derives the same goldens in Node; this is the Python side of the one
wire. `shared/delegation.py` is byte-identical to the contract's canonical copy and to admin-api's
(fact delegation-token), so what holds here holds for the verifier too.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared import delegation as d

GOLDEN = (Path(__file__).resolve().parents[3] / "core" / "identity" / "contracts" / "delegation.v1"
          / "golden")


def _golden(prefix: str) -> list:
    return sorted(GOLDEN.glob(f"{prefix}.*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_the_goldens_are_there():
    assert {p.name for p in _golden("Vector")} >= {"Vector.human.json", "Vector.autonomous.json",
                                                   "Vector.targeted.json"}
    reasons = {_load(p)["reason"] for p in _golden("Refusal")}
    assert reasons == {"not_delegated", "malformed", "bad_signature", "bad_audience", "expired", "revoked"}


@pytest.mark.parametrize("path", _golden("Vector"), ids=lambda p: p.name)
def test_minting_the_inputs_reproduces_the_token(path):
    v = _load(path)
    token = d.mint_delegation(v["secret"], subject=v["subject"], regime=v["regime"],
                              workspaces=v["workspaces"], target=v.get("target", ""),
                              ttl_sec=v["ttl_sec"], now=v["now"], jti=v["jti"])
    assert token == v["token"]
    assert d.verify_delegation(v["secret"], token, now=v["now"]) == v["claims"]


@pytest.mark.parametrize("path", _golden("Refusal"), ids=lambda p: p.name)
def test_a_refusal_vector_is_refused_for_its_reason(path):
    v = _load(path)
    with pytest.raises(d.DelegationError) as e:
        d.verify_delegation(v["secret"], v["token"], now=v["now"], revoked=v.get("revoked"))
    assert e.value.reason == v["reason"], v["what"]


def test_the_vectors_key_is_refused_at_boot():
    """The vectors are made with a key published in this repository, so agent-api's boot preflight
    refuses it as VEXA_MCP_DELEGATION_SECRET (admin-api's declaration carries the same list, fact
    delegation-secret-placeholders) and never echoes it."""
    from control_plane import config_preflight as cp

    key = _load(_golden("Vector")[0])["secret"]
    base = {"INTERNAL_API_SECRET": "a-real-secret",
            "VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE": "/run/vexa-identity/public/key.pem",
            "RUNTIME_API_TOKEN": "runtime-caller-token-for-tests-0123456789abcdef",
            "VEXA_DISPATCH_SIGNING_KEY": "dispatch-signing-key-for-tests-0123456789abcdef"}
    with pytest.raises(cp.ConfigError) as e:
        cp.preflight({**base, "VEXA_MCP_DELEGATION_SECRET": key})
    assert "VEXA_MCP_DELEGATION_SECRET" in str(e.value)
    assert key not in str(e.value)
    cp.preflight({**base, "VEXA_MCP_DELEGATION_SECRET": "a-real-delegation-secret-of-some-length"})
