"""The vendored signer against the contract: it is the canonical file byte for byte, it reproduces
every golden signing vector, and it refuses forged, expired, replayed and mis-bound assertions."""
import json
import time

import pytest

from conftest import CONTRACT
from credential_broker import assertion

VECTORS = sorted((CONTRACT / "golden").glob("SignedAssertionVector.*.json"))


def test_vendored_copy_is_the_canonical_file():
    vendored = (assertion.__file__)
    assert open(vendored, "rb").read() == (CONTRACT / "assertion.py").read_bytes()


@pytest.mark.parametrize("vector", VECTORS, ids=lambda p: p.stem)
def test_reproduces_and_verifies_golden_vectors(vector):
    v = json.loads(vector.read_text())
    c = v["claims"]
    header = assertion.sign(v["key"].encode(), role=c["role"], actor=c["actor"], session=c["session"],
                            method=c["method"], path=c["path"], body=v["request_body"].encode(),
                            at=c["at"], nonce=c["nonce"])
    assert header == v["header"]
    claims = assertion.verify(v["header"], key_for=lambda role: v["key"].encode(), method=c["method"],
                              path=c["path"], body=v["request_body"].encode(), now=c["at"] + 1)
    assert claims == c


def vector():
    return json.loads(VECTORS[0].read_text())


def verify(v, **over):
    c = v["claims"]
    args = dict(key_for=lambda role: v["key"].encode(), method=c["method"], path=c["path"],
                body=v["request_body"].encode(), now=c["at"])
    args.update(over)
    return assertion.verify(over.pop("header", v["header"]), **{k: a for k, a in args.items() if k != "header"})


@pytest.mark.parametrize("change,kind", [
    ({"key_for": lambda role: b"another-key-of-sufficient-length-000000"}, "signature"),
    ({"now": 1760000000 + 31}, "expired"),
    ({"now": 1760000000 - 6}, "expired"),
    ({"method": "POST"}, "binding"),
    ({"path": "/api/connections?x=1"}, "binding"),
    ({"body": b"{}"}, "binding"),
])
def test_refusals_carry_their_kind(change, kind):
    with pytest.raises(assertion.AssertionRefused) as e:
        verify(vector(), **change)
    assert e.value.kind == kind
    assert vector()["claims"]["actor"] not in str(e.value)


def test_replay_refused():
    seen = set()

    def remember(nonce, expires):
        if nonce in seen:
            return False
        seen.add(nonce)
        return True
    v = vector()
    verify(v, remember=remember)
    with pytest.raises(assertion.AssertionRefused) as e:
        verify(v, remember=remember)
    assert e.value.kind == "replay"


def test_claim_tampering_breaks_the_signature():
    v = vector()
    import base64
    claims = dict(v["claims"], actor="victim")
    encoded = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).decode().rstrip("=")
    with pytest.raises(assertion.AssertionRefused) as e:
        verify(v, header=encoded + "." + v["signature"])
    assert e.value.kind == "signature"


@pytest.mark.parametrize("vector", VECTORS, ids=lambda p: p.stem)
def test_every_golden_key_is_published_and_never_loads_as_a_deployment_key(vector, tmp_path):
    """The vectors' keys are fixed fixture values in a public repository. Each is on PUBLISHED_KEYS,
    so a key file still holding one is refused wherever a role key is loaded (the broker, agent-api)."""
    key = json.loads(vector.read_text())["key"].encode()
    assert key in assertion.PUBLISHED_KEYS
    p = tmp_path / "k"
    p.write_bytes(b"  " + key + b"\n")
    with pytest.raises(assertion.PublishedKey):
        assertion.load_key(p)


def test_short_keys_never_sign(tmp_path):
    p = tmp_path / "k"
    p.write_text("too-short")
    with pytest.raises(assertion.KeyUnavailable):
        assertion.load_key(p)
    with pytest.raises(assertion.KeyUnavailable):
        assertion.sign(b"short", role="agent", actor="a", session="s", method="GET", path="/api/x")
    with pytest.raises(assertion.KeyUnavailable):
        assertion.load_key("")


def test_fresh_signature_round_trip():
    key = b"k" * 32
    header = assertion.sign(key, role="human", actor="7", session="s", method="POST", path="/api/setup", body=b"{}")
    assert assertion.verify(header, key_for=lambda r: key, method="POST", path="/api/setup", body=b"{}",
                            now=time.time())["actor"] == "7"
