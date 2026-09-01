# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (dave_voice/transport.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""Transport-layer (client<->SFU) decryption for rtpsize AEAD modes.

Layout (verified against py-cord's own receive path, discord/voice/receive/reader.py
+ discord/voice/packets/rtp.py):
  - AAD (`header`) = the 12-byte RTP header, plus the 4-byte extension preamble
    (profile + length) when the packet carries an extension. The extension *body*
    is part of the ciphertext, not the AAD.
  - `ciphertext` = encrypted payload + auth tag, between the header/preamble and the
    trailing 4-byte nonce.
  - `nonce` = the 4-byte truncated synchronization nonce (last 4 bytes of the UDP
    packet). Discord puts these 4 bytes FIRST in the cipher nonce, then zero-pads to
    the cipher's nonce length (12 for AES-GCM, 24 for XChaCha20-Poly1305).
"""

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl import bindings

SUPPORTED_MODES = (
    "aead_aes256_gcm_rtpsize",
    "aead_xchacha20_poly1305_rtpsize",
)


class TransportCrypto:
    def __init__(self, mode: str, secret_key: bytes):
        if mode not in SUPPORTED_MODES:
            raise ValueError(f"unsupported transport mode: {mode}")
        self.mode = mode
        self.key = bytes(secret_key)

    def decrypt(self, header: bytes, ciphertext: bytes, nonce: bytes) -> bytes:
        """Decrypt one rtpsize AEAD payload. `nonce` is the 4-byte truncated nonce."""
        if self.mode == "aead_aes256_gcm_rtpsize":
            full_nonce = nonce + b"\x00" * 8  # 12-byte GCM nonce, value first
            return AESGCM(self.key).decrypt(full_nonce, ciphertext, header)
        full_nonce = nonce + b"\x00" * 20  # 24-byte XChaCha nonce, value first
        return bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(ciphertext, header, full_nonce, self.key)
