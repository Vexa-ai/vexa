# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (dave_voice/opcodes.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""Voice gateway opcodes and binary frame (de)framing for DAVE."""

import struct
from enum import IntEnum


class VoiceOp(IntEnum):
    IDENTIFY = 0
    SELECT_PROTOCOL = 1
    READY = 2
    HEARTBEAT = 3
    SESSION_DESCRIPTION = 4
    SPEAKING = 5
    HEARTBEAT_ACK = 6
    RESUME = 7
    HELLO = 8
    RESUMED = 9
    CLIENTS_CONNECT = 11
    CLIENT_DISCONNECT = 13
    DAVE_PREPARE_TRANSITION = 21
    DAVE_EXECUTE_TRANSITION = 22
    DAVE_TRANSITION_READY = 23
    DAVE_PREPARE_EPOCH = 24
    DAVE_MLS_EXTERNAL_SENDER = 25
    DAVE_MLS_KEY_PACKAGE = 26
    DAVE_MLS_PROPOSALS = 27
    DAVE_MLS_COMMIT_WELCOME = 28
    DAVE_MLS_ANNOUNCE_COMMIT = 29
    DAVE_MLS_WELCOME = 30
    DAVE_MLS_INVALID_COMMIT_WELCOME = 31


# Server->client binary opcodes carry a 2-byte big-endian sequence prefix.
BINARY_SERVER_OPS = frozenset({25, 27, 29, 30})


def decode_binary(data: bytes) -> tuple[int, int, bytes]:
    """[u16 BE seq][u8 opcode][payload] -> (seq, opcode, payload)."""
    seq = struct.unpack_from(">H", data, 0)[0]
    opcode = data[2]
    return seq, opcode, data[3:]


def encode_binary(opcode: int, payload: bytes) -> bytes:
    """Client->server binary message: [u8 opcode][payload] (no seq prefix)."""
    return bytes([opcode]) + payload
