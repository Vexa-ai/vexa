# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (tests/test_ip_discovery.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
import struct

from discord_bot.dave_voice.ip_discovery import build_request, parse_response


def test_build_request_layout():
    pkt = build_request(0x11223344)
    assert len(pkt) == 74
    typ, length, ssrc = struct.unpack_from(">HHI", pkt, 0)
    assert typ == 0x1
    assert length == 70
    assert ssrc == 0x11223344


def test_parse_response_roundtrip():
    # Build a synthetic response: type=2, len=70, ssrc, 64-byte addr, port
    addr = b"203.0.113.7" + b"\x00" * (64 - len("203.0.113.7"))
    resp = struct.pack(">HHI", 0x2, 70, 0x11223344) + addr + struct.pack(">H", 50001)
    ip, port = parse_response(resp)
    assert ip == "203.0.113.7"
    assert port == 50001
