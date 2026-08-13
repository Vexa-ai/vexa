# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (tests/test_voice_ws_routing.py) under
# a written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
import json
import struct

from discord_bot.dave_voice.opcodes import VoiceOp
from discord_bot.dave_voice.voice_ws import VoiceGateway


class FakeMLS:
    def __init__(self):
        self.calls = []

    def initialize_group(self, version):
        self.calls.append(("init", version))
        return (VoiceOp.DAVE_MLS_KEY_PACKAGE, b"KP")

    def on_external_sender(self, pkg):
        self.calls.append(("ext", pkg))

    def on_prepare_epoch(self, version, epoch):
        self.calls.append(("epoch", version, epoch))
        return (VoiceOp.DAVE_MLS_KEY_PACKAGE, b"KP")

    def on_proposals(self, blob):
        self.calls.append(("proposals", blob))
        return (VoiceOp.DAVE_MLS_COMMIT_WELCOME, b"COMMIT")

    def on_announce_commit(self, tid, commit):
        self.calls.append(("announce", tid, commit))
        return (VoiceOp.DAVE_TRANSITION_READY, json.dumps({"transition_id": tid}).encode())

    def on_welcome(self, tid, welcome):
        self.calls.append(("welcome", tid, welcome))
        return (VoiceOp.DAVE_TRANSITION_READY, json.dumps({"transition_id": tid}).encode())


def make_gw():
    seen = {"ready": None, "sd": None, "speaking": [], "sent": [], "mls_change": 0}
    gw = VoiceGateway(
        endpoint="x",
        server_id=1,
        user_id=2,
        session_id="s",
        token="t",
        mls=FakeMLS(),
        on_ready=lambda d: seen.__setitem__("ready", d),
        on_session_description=lambda d: seen.__setitem__("sd", d),
        on_speaking=lambda uid, ssrc: seen["speaking"].append((uid, ssrc)),
        on_mls_change=lambda: seen.__setitem__("mls_change", seen["mls_change"] + 1),
    )

    async def fake_json(op, d):
        seen["sent"].append(("json", op, d))

    async def fake_binary(op, payload):
        seen["sent"].append(("bin", op, payload))

    gw.send_json = fake_json
    gw.send_binary = fake_binary
    return gw, seen


async def test_ready_and_session_description_and_speaking():
    gw, seen = make_gw()
    await gw._dispatch(
        json.dumps({"op": 2, "d": {"ssrc": 9, "ip": "1.2.3.4", "port": 50, "modes": ["aead_aes256_gcm_rtpsize"]}}),
        is_binary=False,
    )
    await gw._dispatch(
        json.dumps({"op": 4, "d": {"secret_key": [0], "mode": "aead_aes256_gcm_rtpsize", "dave_protocol_version": 1}}),
        is_binary=False,
    )
    await gw._dispatch(json.dumps({"op": 5, "d": {"user_id": "77", "ssrc": 9}}), is_binary=False)
    assert seen["ready"]["ssrc"] == 9
    assert seen["sd"]["dave_protocol_version"] == 1
    assert seen["speaking"] == [(77, 9)]
    # op 4 must init the MLS group and publish the key package (op 26)
    assert ("init", 1) in gw.mls.calls
    assert ("bin", VoiceOp.DAVE_MLS_KEY_PACKAGE, b"KP") in seen["sent"]


async def test_binary_external_sender_updates_seq_and_calls_mls():
    gw, seen = make_gw()
    msg = struct.pack(">H", 12) + bytes([25]) + b"EXTPKG"
    await gw._dispatch(msg, is_binary=True)
    assert gw.last_seq == 12
    assert ("ext", b"EXTPKG") in gw.mls.calls


async def test_binary_proposals_sends_commit():
    gw, seen = make_gw()
    msg = struct.pack(">H", 13) + bytes([27]) + b"PROPS"
    await gw._dispatch(msg, is_binary=True)
    assert ("bin", VoiceOp.DAVE_MLS_COMMIT_WELCOME, b"COMMIT") in seen["sent"]


async def test_binary_welcome_parses_transition_id_and_replies_ready():
    gw, seen = make_gw()
    welcome_body = b"WELCOMEBYTES"
    msg = struct.pack(">H", 20) + bytes([30]) + struct.pack(">H", 5) + welcome_body
    await gw._dispatch(msg, is_binary=True)
    assert ("welcome", 5, welcome_body) in gw.mls.calls
    assert ("json", VoiceOp.DAVE_TRANSITION_READY, {"transition_id": 5}) in seen["sent"]


async def test_prepare_epoch_sends_key_package():
    gw, seen = make_gw()
    await gw._dispatch(json.dumps({"op": 24, "d": {"protocol_version": 1, "epoch": 1}}), is_binary=False)
    assert ("bin", VoiceOp.DAVE_MLS_KEY_PACKAGE, b"KP") in seen["sent"]


async def test_binary_announce_commit_parses_transition_id_and_replies_ready():
    gw, seen = make_gw()
    transition_id = 7
    commit_bytes = b"COMMITBYTES"
    msg = struct.pack(">H", 15) + bytes([29]) + struct.pack(">H", transition_id) + commit_bytes
    await gw._dispatch(msg, is_binary=True)
    assert ("announce", transition_id, commit_bytes) in gw.mls.calls
    assert ("json", VoiceOp.DAVE_TRANSITION_READY, {"transition_id": transition_id}) in seen["sent"]
    # a commit advances the epoch -> ratchets must be refreshed (incl. tid=0 with no op22)
    assert seen["mls_change"] == 1


async def test_clients_connect_and_disconnect_invoke_callbacks():
    connect_calls: list[list[int]] = []
    disconnect_calls: list[int] = []

    gw, seen = make_gw()
    gw.on_clients_connect = lambda user_ids: connect_calls.append(user_ids)
    gw.on_client_disconnect = lambda user_id: disconnect_calls.append(user_id)

    # op 11 — Clients Connect
    await gw._dispatch(
        json.dumps({"op": 11, "d": {"user_ids": ["10", "20"]}}),
        is_binary=False,
    )
    # op 13 — Client Disconnect
    await gw._dispatch(
        json.dumps({"op": 13, "d": {"user_id": "10"}}),
        is_binary=False,
    )

    assert connect_calls == [[10, 20]]
    assert disconnect_calls == [10]
