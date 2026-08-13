# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (tests/test_mls.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
import json

import pytest

import discord_bot.dave_voice.mls as mls_mod
from discord_bot.dave_voice.mls import MLSManager
from discord_bot.dave_voice.opcodes import VoiceOp


class FakeRatchet:
    pass


class FakeSession:
    def __init__(self, *a, **k):
        self.inited = None
        self.external = None
        self.version = 0
        self._ratchets = {}
        self.established = False

    def init(self, version, group_id, self_user_id, transient_key=None):
        self.inited = (version, group_id, self_user_id)
        self.established = True

    def set_protocol_version(self, v):
        self.version = v

    def set_external_sender(self, pkg):
        self.external = pkg

    def get_marshalled_key_package(self):
        return b"KP"

    def process_proposals(self, proposals, recognized):
        return b"COMMIT" if proposals == b"adds" else None

    def process_commit(self, commit):
        return {1: [0]}  # roster-ish success (not a RejectType)

    def process_welcome(self, welcome, recognized):
        return {1: [0]}

    def get_key_ratchet(self, user_id):
        return self._ratchets.get(user_id)

    def has_established_group(self):
        return self.established


class FakeDecryptor:
    def __init__(self):
        self.ratchet = None

    def transition_to_key_ratchet(self, ratchet, transition_expiry=0.0):
        self.ratchet = ratchet


@pytest.fixture(autouse=True)
def patch_dave(monkeypatch):
    monkeypatch.setattr(mls_mod.dave, "Session", FakeSession)
    monkeypatch.setattr(mls_mod.dave, "Decryptor", FakeDecryptor)
    # RejectType used in isinstance checks; mirror the real binding (enum.IntEnum)
    import enum

    class RejectType(enum.IntEnum):  # noqa
        failed = 0
        ignored = 1

    monkeypatch.setattr(mls_mod.dave, "RejectType", RejectType)


def test_prepare_epoch_1_inits_and_returns_key_package():
    m = MLSManager(self_user_id=42)
    m.set_group_id(999)
    m.set_version(1)
    reply = m.on_prepare_epoch(version=1, epoch=1)
    assert reply == (VoiceOp.DAVE_MLS_KEY_PACKAGE, b"KP")
    assert m.session.inited == (1, 999, "42")


def test_prepare_epoch_gt1_no_keypackage():
    m = MLSManager(self_user_id=42)
    m.set_group_id(999)
    m.set_version(1)
    m.on_prepare_epoch(version=1, epoch=1)
    assert m.on_prepare_epoch(version=1, epoch=2) is None


def test_proposals_producing_commit():
    m = MLSManager(self_user_id=42)
    m.set_group_id(999)
    m.set_version(1)
    m.on_prepare_epoch(1, 1)
    reply = m.on_proposals(b"adds")
    assert reply == (VoiceOp.DAVE_MLS_COMMIT_WELCOME, b"COMMIT")


def test_proposals_no_commit_returns_none():
    m = MLSManager(self_user_id=42)
    m.set_group_id(999)
    m.set_version(1)
    m.on_prepare_epoch(1, 1)
    assert m.on_proposals(b"revoke") is None


def test_announce_commit_returns_transition_ready_json():
    m = MLSManager(self_user_id=42)
    m.set_group_id(999)
    m.set_version(1)
    m.on_prepare_epoch(1, 1)
    op, payload = m.on_announce_commit(transition_id=5, commit=b"C")
    assert op == VoiceOp.DAVE_TRANSITION_READY
    assert json.loads(payload) == {"transition_id": 5}


def test_refresh_ratchets_assigns_per_ssrc():
    m = MLSManager(self_user_id=42)
    m.set_group_id(999)
    m.set_version(1)
    m.on_prepare_epoch(1, 1)
    r = FakeRatchet()
    m.session._ratchets["7"] = r
    m.refresh_ratchets({1234: 7})
    assert m.decryptor_for(1234).ratchet is r


def test_announce_commit_returns_invalid_on_reject():
    m = MLSManager(self_user_id=42)
    m.set_group_id(999)
    m.set_version(1)
    m.on_prepare_epoch(1, 1)
    # Override process_commit to return a RejectType instance (failure path)
    m.session.process_commit = lambda commit: mls_mod.dave.RejectType.failed
    op, payload = m.on_announce_commit(transition_id=5, commit=b"C")
    assert op == VoiceOp.DAVE_MLS_INVALID_COMMIT_WELCOME
    assert json.loads(payload) == {"transition_id": 5}


def test_welcome_none_returns_invalid():
    m = MLSManager(self_user_id=42)
    m.set_group_id(999)
    m.set_version(1)
    m.on_prepare_epoch(1, 1)
    # Override process_welcome to return None (failure path)
    m.session.process_welcome = lambda welcome, recognized: None
    op, payload = m.on_welcome(transition_id=8, welcome=b"W")
    assert op == VoiceOp.DAVE_MLS_INVALID_COMMIT_WELCOME
    assert json.loads(payload) == {"transition_id": 8}
