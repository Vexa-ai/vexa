"""gateway-identity.v1 — the signer and verifier, against the contract's own goldens (P8).

The vectors in core/gateway/contracts/gateway-identity.v1/golden are re-signed here in Python (validate.mjs
re-signs them in Node), and every way a token can be wrong is refused with its own reason.
"""
import json
import pathlib

import pytest

from gateway import identity_token as it

GOLDEN = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "gateway-identity.v1" / "golden"
SECRET = "unit-secret"


@pytest.mark.parametrize("name", ["vector-human.json", "vector-delegated.json"])
def test_the_golden_vectors_reproduce(name):
    v = json.loads((GOLDEN / name).read_text())
    assert it.sign(v["secret"], v["claims"], now=v["now"], ttl_sec=v["ttl_sec"]) == v["token"]
    claims = it.verify(v["secret"], v["token"], now=v["now"] + 1)
    assert claims == json.loads((GOLDEN / name.replace("vector-", "claims-")).read_text())


def test_a_token_signed_with_another_key_is_refused():
    tok = it.sign("other", {"sub": "1"}, now=1000)
    with pytest.raises(it.IdentityError) as e:
        it.verify(SECRET, tok, now=1001)
    assert e.value.reason == "bad_signature"


def test_a_tampered_payload_is_refused_before_it_is_read():
    tok = it.sign(SECRET, {"sub": "1"}, now=1000)
    v, payload, sig = tok.split(".")
    forged = it._b64u(json.dumps({"sub": "2", "typ": it.TYPE, "iat": 1000, "exp": 1060}).encode())
    with pytest.raises(it.IdentityError) as e:
        it.verify(SECRET, f"{v}.{forged}.{sig}", now=1001)
    assert e.value.reason == "bad_signature"


def test_expiry_and_skew_are_bounded():
    tok = it.sign(SECRET, {"sub": "1"}, now=1000, ttl_sec=60)
    assert it.verify(SECRET, tok, now=1060 + it.MAX_SKEW_SEC - 1)["sub"] == "1"
    with pytest.raises(it.IdentityError) as e:
        it.verify(SECRET, tok, now=1060 + it.MAX_SKEW_SEC)
    assert e.value.reason == "expired"
    with pytest.raises(it.IdentityError) as e:
        it.verify(SECRET, tok, now=1000 - it.MAX_SKEW_SEC - 1)
    assert e.value.reason == "not_yet_valid"


def test_a_long_lived_token_is_refused_whatever_it_says_about_itself():
    tok = it.sign(SECRET, {"sub": "1"}, now=1000, ttl_sec=it.MAX_TTL_SEC + 1)
    with pytest.raises(it.IdentityError) as e:
        it.verify(SECRET, tok, now=1001)
    assert e.value.reason == "lifetime_too_long"


@pytest.mark.parametrize("token", ["", "garbage", "v2.a.b", "v1.a", "v1.!!.!!"])
def test_malformed_tokens_are_refused(token):
    with pytest.raises(it.IdentityError):
        it.verify(SECRET, token, now=1000)


def test_no_secret_means_no_signer_and_no_verifier():
    with pytest.raises(ValueError):
        it.sign("", {"sub": "1"})
    with pytest.raises(ValueError):
        it.verify("", "v1.a.b")
    with pytest.raises(ValueError):
        it.IdentityGuard(lambda *a: None, secret="")
