# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (dave_voice/udp_receiver.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""asyncio UDP receive endpoint for RTP voice packets."""

import asyncio


class VoiceUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_packet):
        self.on_packet = on_packet
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.on_packet(data)

    def error_received(self, exc):
        print(f"udp error: {exc}", flush=True)


async def open_udp(loop, remote_ip, remote_port, on_packet):
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: VoiceUDPProtocol(on_packet),
        remote_addr=(remote_ip, remote_port),
    )
    return transport, protocol
