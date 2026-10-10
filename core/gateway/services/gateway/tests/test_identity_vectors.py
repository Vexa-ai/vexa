"""gateway-identity.v1's re-entry and claims-to-headers vectors, driven through the gateway's own code.

The contract's `validate.mjs` restates both rules in Node; this holds the Python that runs them to the
same goldens: `delegation.McpReentry.admits` (the MCP re-entry match rule) and
`identity_token.headers_from_claims` (the x-user-* headers a service rebuilds from verified claims).
"""
from __future__ import annotations

import json
import pathlib
import time
from types import SimpleNamespace

import pytest

from gateway import identity_token as it
from gateway.delegation import MCP_REENTRY_HEADER, McpReentry

GOLDEN = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "gateway-identity.v1" / "golden"


def _golden(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text())


def _names(prefix: str) -> list:
    return sorted(p.name for p in GOLDEN.glob(f"{prefix}*.json"))


def test_the_vectors_are_there():
    assert "reentry-admitted.json" in _names("reentry-")
    assert len(_names("reentry-")) >= 5
    assert _names("headers-") == ["headers-delegated.json", "headers-human.json"]


@pytest.mark.parametrize("name", _names("reentry-"))
def test_reentry_admits_exactly_the_vectors_marked_admitted(name):
    """The vector's signed claims, signed now by this edge's key and presented in the re-entry header,
    against the bearer's /internal/validate answer. Signature and lifetime are other vectors' concern;
    this is the match rule: same person, and a delegation equal to the one the edge would sign."""
    v = _golden(name)
    key = it.generate_signing_key()
    claims = {k: c for k, c in v["signed"].items() if k not in ("typ", "iat", "exp")}
    token = it.sign(key, claims, now=int(time.time()))
    request = SimpleNamespace(headers={MCP_REENTRY_HEADER: token})
    assert McpReentry(key).admits(request, v["validation"]) is v["admitted"], v["what"]


def test_reentry_signed_by_another_key_is_not_admitted():
    v = _golden("reentry-admitted.json")
    claims = {k: c for k, c in v["signed"].items() if k not in ("typ", "iat", "exp")}
    token = it.sign(it.generate_signing_key(), claims, now=int(time.time()))
    request = SimpleNamespace(headers={MCP_REENTRY_HEADER: token})
    assert McpReentry(it.generate_signing_key()).admits(request, v["validation"]) is False


@pytest.mark.parametrize("name", _names("headers-"))
def test_the_headers_a_service_rebuilds_are_the_vectors(name):
    v = _golden(name)
    assert it.headers_from_claims(_golden(v["claims_golden"])) == v["headers"]
