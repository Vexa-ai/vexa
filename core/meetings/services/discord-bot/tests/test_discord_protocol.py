# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (tests/test_discord_protocol.py) under
# a written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""Tests for the py-cord VoiceProtocol credential-capture shim.

Root cause being guarded: py-cord routes VOICE_SERVER_UPDATE / VOICE_STATE_UPDATE
to the guild's *registered VoiceProtocol* (vc.on_voice_server_update / on_voice_state_update),
NOT as client-level @bot.event handlers. DAVEVoiceProtocol is that registered protocol,
captures the handshake credentials, and resolves once both arrive.
"""

import asyncio

import pytest

from discord_bot.dave_voice.discord_protocol import DAVEVoiceProtocol


class FakeServerPayload:
    def __init__(self, token, endpoint):
        self.token = token
        self.endpoint = endpoint


class FakeStatePayload:
    def __init__(self, session_id):
        self.session_id = session_id


class FakeGuild:
    """change_voice_state(channel=...) simulates Discord answering with both updates."""

    def __init__(self, proto_box, answer=True):
        self.calls = []
        self._box = proto_box
        self._answer = answer

    async def change_voice_state(self, *, channel):
        self.calls.append(channel)
        proto = self._box[0]
        if channel is not None and self._answer:
            await proto.on_voice_server_update(FakeServerPayload("tok", "ep.discord.media:443"))
            await proto.on_voice_state_update(FakeStatePayload("sess-123"))


class FakeChannel:
    def __init__(self, guild):
        self.guild = guild


def _make(answer=True):
    box = [None]
    guild = FakeGuild(box, answer=answer)
    channel = FakeChannel(guild)
    proto = DAVEVoiceProtocol(client=object(), channel=channel)
    box[0] = proto
    return proto, guild, channel


async def test_captures_credentials_from_payloads():
    proto, _guild, _channel = _make()
    await proto.on_voice_server_update(FakeServerPayload("T", "E"))
    assert not proto._ready.is_set()  # needs the state update too
    await proto.on_voice_state_update(FakeStatePayload("S"))
    assert proto.token == "T"
    assert proto.endpoint == "E"
    assert proto.session_id == "S"
    assert proto._ready.is_set()


async def test_connect_triggers_change_voice_state_and_resolves():
    proto, guild, channel = _make()
    await proto.connect(timeout=2.0)
    assert channel in guild.calls  # change_voice_state(channel=self.channel) was sent (op 4)
    assert proto.token == "tok"
    assert proto.endpoint == "ep.discord.media:443"
    assert proto.session_id == "sess-123"


async def test_connect_times_out_when_no_updates_arrive():
    proto, _guild, _channel = _make(answer=False)
    with pytest.raises(asyncio.TimeoutError):
        await proto.connect(timeout=0.05)


async def test_disconnect_leaves_channel_and_cleans_up():
    proto, guild, _channel = _make()
    cleaned = []
    proto.cleanup = lambda: cleaned.append(True)  # base cleanup touches live client state
    await proto.disconnect(force=True)
    assert None in guild.calls  # change_voice_state(channel=None)
    assert cleaned == [True]
