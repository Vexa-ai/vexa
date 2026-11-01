# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (dave_voice/mls.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""MLS group orchestration over dave.py's Session + per-SSRC Decryptor registry.

Pure routing/state logic: methods return (opcode, payload_bytes) reply tuples or
None; the caller (VoiceGateway) does the actual network send. All RFC-9420 / frame
crypto lives inside dave.py.
"""

import json

import dave

from discord_bot.dave_voice.opcodes import VoiceOp


class MLSManager:
    def __init__(self, self_user_id: int):
        self.self_user_id = self_user_id
        self.session = dave.Session(mls_failure_callback=self._on_mls_failure)
        self.decryptors: dict[int, dave.Decryptor] = {}
        self.recognized_user_ids: set[str] = set()
        self.version = 1
        self.group_id = 0
        self._initialized = False

    def _on_mls_failure(self, a: str, b: str) -> None:
        # Real MLS handshake failures are worth surfacing (rare in steady state).
        print(f"discord-adapter: MLS failure: {a!r} / {b!r}", flush=True)

    def set_group_id(self, group_id: int) -> None:
        self.group_id = group_id

    def set_version(self, version: int) -> None:
        self.version = version
        if self._initialized:
            self.session.set_protocol_version(version)

    def initialize_group(self, version: int):
        """Create (or recreate) the local MLS group and return the op-26 key package.

        Per py-cord's reinit_dave_session (state.py): the group is created on op 4
        (Session Description) when dave_protocol_version > 0, and the client
        immediately publishes its key package so the gateway can add it.
        """
        self.version = version
        if self._initialized:
            self.session.reset()
        self.session.init(version, self.group_id, str(self.self_user_id))
        self._initialized = True
        return (VoiceOp.DAVE_MLS_KEY_PACKAGE, self.session.get_marshalled_key_package())

    def on_external_sender(self, package: bytes) -> None:
        self.session.set_external_sender(package)

    def on_prepare_epoch(self, version: int, epoch: int):
        self.version = version
        if epoch == 1:
            return self.initialize_group(version)
        # epoch > 1: protocol-version change of the existing group
        if self._initialized:
            self.session.set_protocol_version(version)
        return None

    def on_proposals(self, blob: bytes):
        commit = self.session.process_proposals(blob, self.recognized_user_ids)
        if commit is None:
            return None
        return (VoiceOp.DAVE_MLS_COMMIT_WELCOME, commit)

    def _ready_or_invalid(self, transition_id: int, result):
        if isinstance(result, dave.RejectType):
            return (
                VoiceOp.DAVE_MLS_INVALID_COMMIT_WELCOME,
                json.dumps({"transition_id": transition_id}).encode(),
            )
        return (
            VoiceOp.DAVE_TRANSITION_READY,
            json.dumps({"transition_id": transition_id}).encode(),
        )

    def on_announce_commit(self, transition_id: int, commit: bytes):
        result = self.session.process_commit(commit)
        return self._ready_or_invalid(transition_id, result)

    def on_welcome(self, transition_id: int, welcome: bytes):
        result = self.session.process_welcome(welcome, self.recognized_user_ids)
        if result is None:
            return (
                VoiceOp.DAVE_MLS_INVALID_COMMIT_WELCOME,
                json.dumps({"transition_id": transition_id}).encode(),
            )
        return self._ready_or_invalid(transition_id, result)

    def decryptor_for(self, ssrc: int) -> "dave.Decryptor":
        d = self.decryptors.get(ssrc)
        if d is None:
            d = dave.Decryptor()
            self.decryptors[ssrc] = d
        return d

    def refresh_ratchets(self, ssrc_to_user: dict[int, int]) -> None:
        for ssrc, user_id in ssrc_to_user.items():
            ratchet = self.session.get_key_ratchet(str(user_id))
            if ratchet is not None:
                self.decryptor_for(ssrc).transition_to_key_ratchet(ratchet)
