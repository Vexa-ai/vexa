"""delegation.v1's goldens through the rig's own verifier (architecture pass 4, N51).

The rig checks a worker's delegation token with its own copy of the rules (`_verify_delegation`),
not the `delegation.py` agent-api and admin-api vendor byte for byte: it runs from a plain venv with
no control-plane tree on its path. The copy is held to the contract instead. Every vector verifies to
exactly its claims, and every refusal vector is refused for exactly its reason, so a change to the
wire the rig did not follow fails here rather than on a worker's first tool call.
"""
from __future__ import annotations

import json
import pathlib

import pytest

import vexa_control_mcp as rig

GOLDEN = pathlib.Path(__file__).resolve().parents[4] / "core/identity/contracts/delegation.v1/golden"


def _golden(prefix: str) -> list:
    return sorted(GOLDEN.glob(f"{prefix}.*.json"))


def _at(monkeypatch, v: dict, revoked=()) -> None:
    monkeypatch.setattr(rig, "DELEGATION_SECRET", v["secret"])
    monkeypatch.setattr(rig.time, "time", lambda: v["now"])
    monkeypatch.setattr(rig, "_revoked_jtis", lambda: set(revoked))


def test_the_goldens_are_there():
    assert len(_golden("Vector")) >= 3
    assert {json.loads(p.read_text())["reason"] for p in _golden("Refusal")} == {
        "not_delegated", "malformed", "bad_signature", "bad_audience", "expired", "revoked"}


@pytest.mark.parametrize("path", _golden("Vector"), ids=lambda p: p.name)
def test_a_vector_verifies_to_its_claims(monkeypatch, path):
    v = json.loads(path.read_text())
    _at(monkeypatch, v)
    assert rig._is_delegation_token(v["token"])
    assert rig._verify_delegation(v["token"]) == v["claims"]


@pytest.mark.parametrize("path", _golden("Refusal"), ids=lambda p: p.name)
def test_a_refusal_vector_is_refused_for_its_reason(monkeypatch, path):
    v = json.loads(path.read_text())
    if v["reason"] == "not_delegated":
        assert not rig._is_delegation_token(v["token"])
        return
    _at(monkeypatch, v, v.get("revoked", ()))
    assert rig._is_delegation_token(v["token"])
    with pytest.raises(rig._DelegationRefused) as refused:
        rig._verify_delegation(v["token"])
    assert refused.value.reason == v["reason"]
