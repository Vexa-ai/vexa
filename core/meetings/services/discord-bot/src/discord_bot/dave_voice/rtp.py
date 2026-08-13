# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (dave_voice/rtp.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""Minimal RTP header parsing for received Discord voice packets."""

import struct
from dataclasses import dataclass

HEADER_LEN = 12


@dataclass
class RtpPacket:
    version_flags: int
    payload_type: int
    sequence: int
    timestamp: int
    ssrc: int
    header: bytes
    body: bytes


def parse_rtp_header(data: bytes) -> RtpPacket:
    vf, pt, seq, ts, ssrc = struct.unpack_from(">BBHII", data, 0)
    return RtpPacket(
        version_flags=vf,
        payload_type=pt,
        sequence=seq,
        timestamp=ts,
        ssrc=ssrc,
        header=data[:HEADER_LEN],
        body=data[HEADER_LEN:],
    )
