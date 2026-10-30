# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (dave_voice/voice_ws.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""Voice WebSocket gateway v8: lifecycle, heartbeat+seq_ack, and opcode routing."""

import asyncio
import itertools
import json
import struct

import dave
import websockets

from discord_bot.dave_voice.opcodes import (
    VoiceOp,
    decode_binary,
    encode_binary,
)

# JSON-format client->server opcodes (sent as text even when produced by MLSManager).
_JSON_REPLY_OPS = {int(VoiceOp.DAVE_TRANSITION_READY), int(VoiceOp.DAVE_MLS_INVALID_COMMIT_WELCOME)}


class VoiceGateway:
    def __init__(
        self,
        *,
        endpoint,
        server_id,
        user_id,
        session_id,
        token,
        mls,
        on_ready,
        on_session_description,
        on_speaking,
        on_execute=None,
        on_prepare_passthrough=None,
        on_clients_connect=None,
        on_client_disconnect=None,
        on_mls_change=None,
    ):
        self.endpoint = endpoint
        self.server_id = server_id
        self.user_id = user_id
        self.session_id = session_id
        self.token = token
        self.mls = mls
        self.on_ready = on_ready
        self.on_session_description = on_session_description
        self.on_speaking = on_speaking
        self.on_execute = on_execute or (lambda tid: None)
        self.on_prepare_passthrough = on_prepare_passthrough or (lambda: None)
        self.on_clients_connect = on_clients_connect or (lambda user_ids: None)
        self.on_client_disconnect = on_client_disconnect or (lambda user_id: None)
        self.on_mls_change = on_mls_change or (lambda: None)
        self.ws = None
        self.last_seq = 0
        self._hb_nonce = itertools.count(1)
        self._heartbeat_task = None
        self._recv_task = None

    async def connect(self):
        url = f"wss://{self.endpoint}?v=8"
        self.ws = await websockets.connect(url, max_size=None)
        await self.send_json(
            VoiceOp.IDENTIFY,
            {
                "server_id": str(self.server_id),
                "user_id": str(self.user_id),
                "session_id": self.session_id,
                "token": self.token,
                "max_dave_protocol_version": dave.get_max_supported_protocol_version(),
            },
        )
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def close(self):
        for t in (self._heartbeat_task, self._recv_task):
            if t:
                t.cancel()
        if self.ws:
            await self.ws.close()

    async def send_json(self, op, d):
        await self.ws.send(json.dumps({"op": int(op), "d": d}))

    async def send_binary(self, op, payload):
        await self.ws.send(encode_binary(int(op), payload))

    async def _recv_loop(self):
        try:
            async for message in self.ws:
                is_binary = isinstance(message, (bytes, bytearray))
                await self._dispatch(message, is_binary=is_binary)
        except asyncio.CancelledError:
            pass

    async def _heartbeat_loop(self, interval_ms):
        try:
            while True:
                await asyncio.sleep(interval_ms / 1000)
                await self.send_json(
                    VoiceOp.HEARTBEAT,
                    {
                        "t": next(self._hb_nonce),
                        "seq_ack": self.last_seq,
                    },
                )
        except asyncio.CancelledError:
            pass

    async def _send_reply(self, reply):
        if reply is None:
            return
        op, payload = reply
        if int(op) in _JSON_REPLY_OPS:
            await self.send_json(op, json.loads(payload.decode()))
        else:
            await self.send_binary(op, payload)

    async def _dispatch(self, message, is_binary):
        if is_binary:
            seq, opcode, payload = decode_binary(message)
            self.last_seq = seq
            if opcode == VoiceOp.DAVE_MLS_EXTERNAL_SENDER:
                self.mls.on_external_sender(payload)
            elif opcode == VoiceOp.DAVE_MLS_PROPOSALS:
                await self._send_reply(self.mls.on_proposals(payload))
            elif opcode == VoiceOp.DAVE_MLS_ANNOUNCE_COMMIT:
                tid = struct.unpack_from(">H", payload, 0)[0]
                await self._send_reply(self.mls.on_announce_commit(tid, payload[2:]))
                # a commit advances the epoch (and, for tid=0, executes immediately
                # with no op22) — rewire per-sender key ratchets now.
                self.on_mls_change()
            elif opcode == VoiceOp.DAVE_MLS_WELCOME:
                tid = struct.unpack_from(">H", payload, 0)[0]
                await self._send_reply(self.mls.on_welcome(tid, payload[2:]))
                self.on_mls_change()
            return

        frame = json.loads(message)
        op = frame.get("op")
        d = frame.get("d") or {}
        if "seq" in frame and frame["seq"] is not None:
            self.last_seq = frame["seq"]

        if op == VoiceOp.HELLO:
            interval = d["heartbeat_interval"]
            if self._heartbeat_task is None:
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval))
        elif op == VoiceOp.READY:
            self.on_ready(d)
        elif op == VoiceOp.SESSION_DESCRIPTION:
            self.on_session_description(d)
            # py-cord inits the MLS group here (reinit_dave_session on op 4) and
            # immediately publishes its key package so the gateway can add us.
            if (d.get("dave_protocol_version", 0) or 0) >= 1:
                await self._send_reply(self.mls.initialize_group(d["dave_protocol_version"]))
        elif op == VoiceOp.SPEAKING:
            self.on_speaking(int(d["user_id"]), int(d["ssrc"]))
        elif op == VoiceOp.DAVE_PREPARE_EPOCH:
            await self._send_reply(self.mls.on_prepare_epoch(d["protocol_version"], d["epoch"]))
        elif op == VoiceOp.DAVE_PREPARE_TRANSITION:
            tid = d.get("transition_id", 0)
            if d.get("protocol_version") == 0:
                self.on_prepare_passthrough()
            await self.send_json(VoiceOp.DAVE_TRANSITION_READY, {"transition_id": tid})
        elif op == VoiceOp.DAVE_EXECUTE_TRANSITION:
            self.on_execute(d.get("transition_id", 0))
        elif op == VoiceOp.CLIENTS_CONNECT:
            self.on_clients_connect([int(u) for u in d.get("user_ids", [])])
        elif op == VoiceOp.CLIENT_DISCONNECT:
            self.on_client_disconnect(int(d["user_id"]))
        # op 6 (ack), 9 (resumed) -> no-op here
