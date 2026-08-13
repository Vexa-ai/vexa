# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (dave_voice/opus_decode.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""Per-SSRC Opus decoding to 48 kHz 16-bit stereo PCM, via py-cord's libopus binding."""

from typing import Any


def _default_factory():
    import discord

    return discord.opus.Decoder()


class OpusDecoders:
    def __init__(self, decoder_factory=_default_factory):
        self._factory = decoder_factory
        self._decoders: dict[int, Any] = {}

    def decode(self, ssrc: int, opus_bytes: bytes) -> bytes:
        dec = self._decoders.get(ssrc)
        if dec is None:
            dec = self._factory()
            self._decoders[ssrc] = dec
        # fec=False: decode this packet's audio normally. The binding defaults to
        # fec=True, which decodes in-band Forward Error Correction (a redundant copy
        # of the PREVIOUS frame) instead of the current frame -> garbled output.
        return dec.decode(opus_bytes, fec=False)

    def reset(self, ssrc: int) -> None:
        self._decoders.pop(ssrc, None)
