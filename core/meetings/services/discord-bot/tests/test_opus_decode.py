# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (tests/test_opus_decode.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
from discord_bot.dave_voice.opus_decode import OpusDecoders


class FakeDecoder:
    def __init__(self):
        self.calls = []
        self.fec_flags = []

    def decode(self, data, *, fec=True):
        self.calls.append(data)
        self.fec_flags.append(fec)
        # pretend each opus frame -> 4 bytes of PCM
        return b"\x00\x00\x01\x01"


def test_one_decoder_per_ssrc_and_decode():
    made = []

    def factory():
        d = FakeDecoder()
        made.append(d)
        return d

    od = OpusDecoders(decoder_factory=factory)
    out1 = od.decode(111, b"opusA")
    od.decode(111, b"opusB")
    od.decode(222, b"opusC")
    assert out1 == b"\x00\x00\x01\x01"
    assert len(made) == 2  # one per distinct ssrc
    assert made[0].calls == [b"opusA", b"opusB"]
    assert made[1].calls == [b"opusC"]
    # normal packets must decode with fec=False (fec=True garbles to FEC data)
    assert made[0].fec_flags == [False, False]


def test_reset_drops_decoder():
    od = OpusDecoders(decoder_factory=lambda: FakeDecoder())
    od.decode(111, b"x")
    od.reset(111)
    assert 111 not in od._decoders
