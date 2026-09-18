# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (dave_voice/discord_protocol.py) under
# a written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""py-cord VoiceProtocol shim that captures the voice handshake credentials.

py-cord (2.8) does NOT dispatch VOICE_SERVER_UPDATE / VOICE_STATE_UPDATE as
client-level events. `ConnectionState.parse_voice_server_update` /
`parse_voice_state_update` route them to the guild's *registered* VoiceProtocol
(`vc.on_voice_server_update(payload)` / `vc.on_voice_state_update(payload)`), and
only if one is registered via `channel.connect(cls=...)`. So we register this
shim purely to capture the `token`, `endpoint`, and `session_id`; the actual
voice WebSocket + UDP receive is handled entirely by DAVEVoiceClient.
"""

import asyncio
from typing import cast

import discord
from discord.voice import VoiceProtocol


class DAVEVoiceProtocol(VoiceProtocol):
    """Captures the voice connection credentials, then gets out of the way.

    `channel.connect(cls=DAVEVoiceProtocol)` instantiates and registers this, then
    calls `connect()`. We send the gateway voice-state-update (op 4) and wait for
    Discord to answer with both the server update (token/endpoint) and the state
    update (session_id). The caller reads `.token`/`.endpoint`/`.session_id`.
    """

    def __init__(self, client, channel):
        super().__init__(client, channel)
        self.token: str | None = None
        self.endpoint: str | None = None
        self.session_id: str | None = None
        self._got_server = False
        self._got_state = False
        self._ready = asyncio.Event()

    def _maybe_ready(self) -> None:
        if self._got_server and self._got_state:
            self._ready.set()

    async def on_voice_server_update(self, data) -> None:
        self.token = data.token
        self.endpoint = data.endpoint
        self._got_server = True
        self._maybe_ready()

    async def on_voice_state_update(self, data) -> None:
        if data.session_id:
            self.session_id = data.session_id
            self._got_state = True
            self._maybe_ready()

    async def connect(self, *, timeout: float = 20.0, reconnect: bool = True, **kwargs) -> None:
        # Sends gateway op 4; Discord replies with VOICE_STATE_UPDATE + VOICE_SERVER_UPDATE,
        # which the library delivers to the two handlers above (we are now the registered vc).
        channel = cast(discord.VoiceChannel, self.channel)
        await channel.guild.change_voice_state(channel=channel)
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    async def disconnect(self, *, force: bool = False) -> None:
        try:
            channel = cast(discord.VoiceChannel, self.channel)
            await channel.guild.change_voice_state(channel=None)
        finally:
            self.cleanup()
