"""The published Ed25519 test key the gateway-identity.v1 signing vectors are made with.

The goldens carry the public key, the claims and the expected token, never the signing key: a test
that signs derives it here from the seed RFC 8032 prints. ``contracts/gateway-identity.v1/validate.mjs``
derives the same key from the same seed in Node. Every service refuses this key as its configured
one (``identity_token.PUBLISHED_TEST_KEYS``).
"""
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

#: RFC 8032 section 7.1 TEST 1, the 32-byte seed RFC 8032 lists as its "SECRET KEY". Public test data.
RFC8032_TEST1_SEED_HEX = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"


def rfc8032_test1_signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(RFC8032_TEST1_SEED_HEX))
