# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (tests/test_rtp.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
import struct

from discord_bot.dave_voice.rtp import HEADER_LEN, parse_rtp_header


def test_parse_header_fields():
    header = struct.pack(">BBHII", 0x80, 0x78, 1234, 0xAABBCCDD, 0x01020304)
    body = b"opuspayload"
    pkt = parse_rtp_header(header + body)
    assert pkt.version_flags == 0x80
    assert pkt.payload_type == 0x78
    assert pkt.sequence == 1234
    assert pkt.timestamp == 0xAABBCCDD
    assert pkt.ssrc == 0x01020304
    assert pkt.header == header
    assert pkt.body == body
    assert HEADER_LEN == 12
