# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (tests/test_transport.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
import os
import struct

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl import bindings

from discord_bot.dave_voice.transport import SUPPORTED_MODES, TransportCrypto


def _rtp_header():
    return struct.pack(">BBHII", 0x80, 0x78, 1, 2, 3)


def test_aes256_gcm_rtpsize_roundtrip():
    key = os.urandom(32)
    header = _rtp_header()
    plaintext = b"decoded-opus-frame-bytes"
    nonce4 = struct.pack(">I", 5)
    # nonce value FIRST, then zero pad to 12 bytes (Discord/py-cord convention)
    ct = AESGCM(key).encrypt(nonce4 + b"\x00" * 8, plaintext, header)
    out = TransportCrypto("aead_aes256_gcm_rtpsize", key).decrypt(header, ct, nonce4)
    assert out == plaintext


def test_xchacha20_poly1305_rtpsize_roundtrip():
    key = os.urandom(32)
    header = _rtp_header()
    plaintext = b"decoded-opus-frame-bytes"
    nonce4 = struct.pack(">I", 9)
    full_nonce = nonce4 + b"\x00" * 20  # value first, then zero pad to 24 bytes
    ct = bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(plaintext, header, full_nonce, key)
    out = TransportCrypto("aead_xchacha20_poly1305_rtpsize", key).decrypt(header, ct, nonce4)
    assert out == plaintext


def test_wrong_nonce_order_does_not_authenticate():
    # Guards the exact bug we hit live: a zeros-first nonce must NOT decrypt.
    key = os.urandom(32)
    header = _rtp_header()
    nonce4 = struct.pack(">I", 7)
    ct = AESGCM(key).encrypt(b"\x00" * 8 + nonce4, b"payload", header)  # wrong order
    with pytest.raises(InvalidTag):
        TransportCrypto("aead_aes256_gcm_rtpsize", key).decrypt(header, ct, nonce4)


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        TransportCrypto("xsalsa20_poly1305", os.urandom(32))


def test_supported_modes_preference_order():
    assert SUPPORTED_MODES[0] == "aead_aes256_gcm_rtpsize"
