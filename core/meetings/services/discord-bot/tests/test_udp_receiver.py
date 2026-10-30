# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (tests/test_udp_receiver.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
from discord_bot.dave_voice.udp_receiver import VoiceUDPProtocol


def test_datagram_received_invokes_callback():
    got = []
    proto = VoiceUDPProtocol(on_packet=lambda data: got.append(data))
    proto.datagram_received(b"rtp-bytes", ("1.2.3.4", 5000))
    assert got == [b"rtp-bytes"]
