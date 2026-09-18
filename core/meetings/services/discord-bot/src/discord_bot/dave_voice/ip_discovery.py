# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (dave_voice/ip_discovery.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""Discord UDP IP discovery (find our public ip/port for Select Protocol)."""

import struct


def build_request(ssrc: int) -> bytes:
    # [u16 type=1][u16 length=70][u32 ssrc][64 addr zeros][u16 port=0]
    return struct.pack(">HHI", 0x1, 70, ssrc) + b"\x00" * 64 + struct.pack(">H", 0)


def parse_response(data: bytes) -> tuple[str, int]:
    # type(2) length(2) ssrc(4) addr(64) port(2)
    addr = data[8:72].split(b"\x00", 1)[0].decode("ascii")
    port = struct.unpack_from(">H", data, 72)[0]
    return addr, port
