"""gateway-identity.v1 — the Ed25519 signer and verifier, against the contract's own goldens (P8).

The vectors in core/gateway/contracts/gateway-identity.v1/golden are re-signed here in Python (validate.mjs
re-signs them in Node) with the RFC 8032 TEST 1 key derived from its published seed (``rfc8032.py``;
no golden carries a signing key), the refusal vectors are refused with their own reason, and the key
split is pinned: only the private key signs, a verifier holds the public key and nothing else, and
every other kind of key — or no key, or a published test key — is refused where it is loaded.
"""
import base64
import hashlib
import hmac
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, rsa, x25519

from gateway import identity_token as it
from rfc8032 import rfc8032_test1_signing_key

GOLDEN = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "gateway-identity.v1" / "golden"
KEY = it.generate_signing_key()
PUB = KEY.public_key()


def _golden(name):
    return json.loads((GOLDEN / name).read_text())


@pytest.mark.parametrize("name", ["vector-human.json", "vector-delegated.json"])
def test_the_golden_vectors_reproduce(name):
    v = _golden(name)
    key = rfc8032_test1_signing_key()
    assert it.public_key_pem(key).decode() == v["public_key"]
    assert it.sign(key, v["claims"], now=v["now"], ttl_sec=v["ttl_sec"]) == v["token"]
    claims = it.verify(it.load_verify_key(v["public_key"]), v["token"], now=v["now"] + 1)
    assert claims == _golden(name.replace("vector-", "claims-"))


@pytest.mark.parametrize("name", sorted(p.name for p in GOLDEN.glob("refused-*.json")))
def test_the_refusal_vectors_are_refused_for_their_reason(name):
    v = _golden(name)
    with pytest.raises(it.IdentityError) as e:
        it.verify(it.load_verify_key(v["public_key"]), v["token"], now=v["now"])
    assert e.value.reason == v["reason"], v["what"]


def test_there_are_refusal_vectors_for_every_attack_this_contract_names():
    names = {p.name for p in GOLDEN.glob("refused-*.json")}
    assert {"refused-other-key.json", "refused-tampered.json", "refused-hmac-over-public-key.json",
            "refused-unsigned.json", "refused-other-version.json", "refused-lifetime.json"} <= names


# ── published test keys ────────────────────────────────────────────────────────────────────────
PRIVATE_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


@pytest.mark.parametrize("name", sorted(p.name for p in GOLDEN.glob("*.json")))
def test_no_golden_carries_a_signing_key_and_every_golden_key_is_refused_at_boot(name):
    """The goldens hold public keys only, and each of them is on the denylist every service checks
    its configured key against, so no deployment can be set up with a key copied from here."""
    text = (GOLDEN / name).read_text()
    assert not PRIVATE_PEM.search(text)
    v = json.loads(text)
    if "public_key" in v:
        assert it.published_test_key(it.load_verify_key(v["public_key"])) == "RFC 8032 section 7.1 TEST 1"


def test_the_published_test_keys_are_the_rfc_vectors():
    assert it.published_test_key(rfc8032_test1_signing_key()) == "RFC 8032 section 7.1 TEST 1"
    assert it.published_test_key(rfc8032_test1_signing_key().public_key()) == "RFC 8032 section 7.1 TEST 1"
    assert it.published_test_key(KEY) is None and it.published_test_key(PUB) is None
    for pem in it.PUBLISHED_TEST_KEYS.values():
        assert it.published_test_key(it.load_verify_key(pem))
    # TEST 2 is the key the "another key" refusal vector was signed with
    other = _golden("refused-other-key.json")
    test2 = it.load_verify_key(it.PUBLISHED_TEST_KEYS["RFC 8032 section 7.1 TEST 2"])
    assert it.verify(test2, other["token"], now=other["now"])["sub"]


@pytest.mark.parametrize("name", sorted(it.PUBLISHED_TEST_KEYS))
def test_a_published_test_key_is_refused_as_the_configured_key(tmp_path, name):
    """The signer and every verifier load their configured key through read_signing_key /
    read_verify_key, which refuse a published key whichever half is mounted, naming the vector and
    never the key."""
    public = tmp_path / "public.pem"
    public.write_text(it.PUBLISHED_TEST_KEYS[name])
    with pytest.raises(it.KeyUnavailable) as e:
        it.read_verify_key(public)
    assert name in str(e.value) and "BEGIN" not in str(e.value)
    if name == "RFC 8032 section 7.1 TEST 1":
        signing = tmp_path / "signing.pem"
        signing.write_bytes(it.private_key_pem(rfc8032_test1_signing_key()))
        with pytest.raises(it.KeyUnavailable) as e:
            it.read_signing_key(signing)
        assert name in str(e.value) and "BEGIN" not in str(e.value)
        with pytest.raises(it.KeyUnavailable):
            it.write_keypair(str(signing), str(tmp_path / "healed-public.pem"))


# ── the key split ──────────────────────────────────────────────────────────────────────────────
def test_a_verifier_cannot_sign():
    """The services behind the gateway hold the public key. It cannot make a signature: sign()
    refuses anything but the gateway's private key."""
    with pytest.raises(TypeError):
        it.sign(PUB, {"sub": "1"})
    with pytest.raises(TypeError):
        it.signed_headers(PUB, {"user_id": 1})
    with pytest.raises(TypeError):
        it.sign(b"a-shared-secret-like-the-old-hmac", {"sub": "1"})


def test_only_the_private_key_signs_and_the_public_key_verifies():
    tok = it.sign(KEY, {"sub": "1"}, now=1000)
    assert it.verify(PUB, tok, now=1001)["sub"] == "1"
    with pytest.raises(TypeError):
        it.verify(KEY, tok, now=1001)  # a verifier is handed the public key, never the private one
    with pytest.raises(TypeError):
        it.verify("a-shared-secret", tok, now=1001)


def test_a_signature_by_another_key_is_refused():
    tok = it.sign(it.generate_signing_key(), {"sub": "1"}, now=1000)
    with pytest.raises(it.IdentityError) as e:
        it.verify(PUB, tok, now=1001)
    assert e.value.reason == "bad_signature"


def test_a_tampered_payload_is_refused_before_it_is_read():
    tok = it.sign(KEY, {"sub": "1"}, now=1000)
    v, payload, sig = tok.split(".")
    forged = it._b64u(json.dumps({"sub": "2", "typ": it.TYPE, "iat": 1000, "exp": 1060}).encode())
    with pytest.raises(it.IdentityError) as e:
        it.verify(PUB, f"{v}.{forged}.{sig}", now=1001)
    assert e.value.reason == "bad_signature"


def test_alg_confusion_is_refused():
    """The token names no algorithm, so there is nothing to confuse: an HMAC keyed with the public
    key (the classic RS256→HS256 move), an empty signature, a signature of the wrong length and any
    scheme but v1 are all refused."""
    tok = it.sign(KEY, {"sub": "1"}, now=1000)
    v, payload, sig = tok.split(".")
    for secret in (it.public_key_pem(KEY), PUB.public_bytes(serialization.Encoding.Raw,
                                                            serialization.PublicFormat.Raw)):
        mac = hmac.new(secret, f"{v}.{payload}".encode(), hashlib.sha256).digest()
        with pytest.raises(it.IdentityError) as e:
            it.verify(PUB, f"{v}.{payload}.{it._b64u(mac)}", now=1001)
        assert e.value.reason == "bad_signature"
    for bad in (f"{v}.{payload}.", f"{v}.{payload}", f"none.{payload}.{sig}", f"v2.{payload}.{sig}",
                f"HS256.{payload}.{sig}"):
        with pytest.raises(it.IdentityError) as e:
            it.verify(PUB, bad, now=1001)
        assert e.value.reason == "malformed"
    with pytest.raises(it.IdentityError) as e:
        it.verify(PUB, f"{v}.{payload}.{it._b64u(base64.b64decode(sig + '==', altchars=b'-_')[:63])}",
                  now=1001)
    assert e.value.reason == "bad_signature"


def test_one_signature_has_one_spelling():
    """Base64url is strict and canonical: padding, foreign characters and a non-canonical last
    character are malformed, so a valid token cannot be re-spelled into a second valid token."""
    tok = it.sign(KEY, {"sub": "1"}, now=1000)
    v, payload, sig = tok.split(".")
    # 64 bytes is 86 base64url characters whose last one carries 4 zero bits, so it is one of
    # A/Q/g/w; its neighbour decodes to the same bytes under a lenient decoder.
    respelled = sig[:-1] + {"A": "B", "Q": "R", "g": "h", "w": "x"}[sig[-1]]
    for bad in (f"{v}.{payload}.{sig}==", f"{v}.{payload}.{respelled}",
                f"{v}.{payload}+.{sig}", f"{v}.{payload}.{sig[:10]}/{sig[11:]}"):
        with pytest.raises(it.IdentityError) as e:
            it.verify(PUB, bad, now=1001)
        assert e.value.reason == "malformed"


def test_expiry_and_skew_are_bounded():
    tok = it.sign(KEY, {"sub": "1"}, now=1000, ttl_sec=60)
    assert it.verify(PUB, tok, now=1060 + it.MAX_SKEW_SEC - 1)["sub"] == "1"
    with pytest.raises(it.IdentityError) as e:
        it.verify(PUB, tok, now=1060 + it.MAX_SKEW_SEC)
    assert e.value.reason == "expired"
    with pytest.raises(it.IdentityError) as e:
        it.verify(PUB, tok, now=1000 - it.MAX_SKEW_SEC - 1)
    assert e.value.reason == "not_yet_valid"


def test_a_long_lived_token_is_refused_whatever_it_says_about_itself():
    tok = it.sign(KEY, {"sub": "1"}, now=1000, ttl_sec=it.MAX_TTL_SEC + 1)
    with pytest.raises(it.IdentityError) as e:
        it.verify(PUB, tok, now=1001)
    assert e.value.reason == "lifetime_too_long"


@pytest.mark.parametrize("token", ["", "garbage", "v2.a.b", "v1.a", "v1.!!.!!"])
def test_malformed_tokens_are_refused(token):
    with pytest.raises(it.IdentityError):
        it.verify(PUB, token, now=1000)


# ── loading keys ───────────────────────────────────────────────────────────────────────────────
def _pem_private(k):
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption())


def _pem_public(k):
    return k.public_key().public_bytes(serialization.Encoding.PEM,
                                       serialization.PublicFormat.SubjectPublicKeyInfo)


OTHER_KEYS = {
    "rsa": rsa.generate_private_key(public_exponent=65537, key_size=2048),
    "p256": ec.generate_private_key(ec.SECP256R1()),
    "ed448": ed448.Ed448PrivateKey.generate(),
    "x25519": x25519.X25519PrivateKey.generate(),
}


@pytest.mark.parametrize("kind", sorted(OTHER_KEYS))
def test_any_key_but_ed25519_is_refused_on_both_sides(kind):
    k = OTHER_KEYS[kind]
    with pytest.raises(it.KeyUnavailable):
        it.load_signing_key(_pem_private(k))
    with pytest.raises(it.KeyUnavailable):
        it.load_verify_key(_pem_public(k))


def test_a_verifier_refuses_to_load_the_private_key():
    """A verifier mounted with the signing key could sign; the loader refuses it rather than derive
    the public half, so the misconfiguration stops the boot."""
    with pytest.raises(it.KeyUnavailable) as e:
        it.load_verify_key(it.private_key_pem(KEY))
    assert "PUBLIC" in str(e.value)


@pytest.mark.parametrize("material", ["", "a-shared-hmac-secret", "0" * 64,
                                      "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n"])
def test_a_secret_or_junk_is_not_a_key(material):
    with pytest.raises(it.KeyUnavailable):
        it.load_verify_key(material)
    with pytest.raises(it.KeyUnavailable):
        it.load_signing_key(material)


def test_the_signing_key_is_not_a_verification_key_either():
    with pytest.raises(it.KeyUnavailable):
        it.load_signing_key(it.public_key_pem(KEY))


def test_key_files_name_the_fault_never_the_key(tmp_path):
    with pytest.raises(it.KeyUnavailable):
        it.read_verify_key("")
    with pytest.raises(it.KeyUnavailable):
        it.read_verify_key(tmp_path / "missing.pem")
    p = tmp_path / "signing.pem"
    p.write_bytes(it.private_key_pem(KEY))
    with pytest.raises(it.KeyUnavailable) as e:
        it.read_verify_key(p)
    assert "BEGIN" not in str(e.value)
    assert isinstance(it.read_signing_key(p), type(KEY))


def test_the_guard_holds_the_public_key_and_nothing_else():
    with pytest.raises(TypeError):
        it.IdentityGuard(lambda *a: None, verify_key=None)
    with pytest.raises(TypeError):
        it.IdentityGuard(lambda *a: None, verify_key=KEY)
    with pytest.raises(TypeError):
        it.IdentityGuard(lambda *a: None, verify_key="a-shared-secret")
    it.IdentityGuard(lambda *a: None, verify_key=PUB)


# ── the install-time generator ─────────────────────────────────────────────────────────────────
def test_keygen_writes_a_pair_and_keeps_it(tmp_path):
    signing, public = tmp_path / "s" / "key.pem", tmp_path / "p" / "key.pem"
    assert it.write_keypair(str(signing), str(public)) is True
    first = signing.read_bytes()
    assert it.public_key_pem(it.read_signing_key(signing)) == public.read_bytes()
    assert oct(os.stat(signing).st_mode & 0o777) == "0o600"
    assert oct(os.stat(public).st_mode & 0o777) == "0o644"
    public.unlink()  # a lost public half heals from the signing key; the signing key is kept
    assert it.write_keypair(str(signing), str(public)) is False
    assert signing.read_bytes() == first
    assert it.public_key_pem(it.read_signing_key(signing)) == public.read_bytes()


def test_keygen_never_replaces_a_signing_key_it_cannot_read(tmp_path):
    signing, public = tmp_path / "key.pem", tmp_path / "pub.pem"
    signing.write_text("not a key")
    with pytest.raises(it.KeyUnavailable):
        it.write_keypair(str(signing), str(public))
    assert signing.read_text() == "not a key"


def test_keygen_cli_prints_no_key(tmp_path):
    src = pathlib.Path(it.__file__)
    out = subprocess.run([sys.executable, str(src), "keygen", str(tmp_path / "s.pem"), str(tmp_path / "p.pem")],
                         capture_output=True, text=True, check=True)
    assert "generated" in out.stdout
    assert "BEGIN" not in out.stdout + out.stderr
    assert it.read_verify_key(tmp_path / "p.pem") is not None
