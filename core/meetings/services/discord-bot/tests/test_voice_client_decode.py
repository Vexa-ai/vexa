# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (tests/test_voice_client_decode.py)
# under a written Apache-2.0 license grant from the original author (Renzo Franceschini) for
# in-tree contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
import struct

from discord_bot.dave_voice.voice_client import DAVEVoiceClient


class FakeTransport:
    def decrypt(self, header, ciphertext, nonce):
        return b"CIPHER->" + ciphertext


class FakeDecryptor:
    def decrypt(self, media_type, frame):
        return b"OPUS:" + frame


class FakeMLS:
    def __init__(self):
        self._d = FakeDecryptor()
        self.recognized_user_ids: set[str] = set()
        self.decryptors: dict[int, object] = {}

    def decryptor_for(self, ssrc):
        return self._d

    def refresh_ratchets(self, mapping):
        pass


class FakeOpus:
    def __init__(self):
        self._resets: list[int] = []

    def decode(self, ssrc, opus_bytes):
        return b"PCM(" + opus_bytes + b")"

    def reset(self, ssrc: int) -> None:
        self._resets.append(ssrc)


def test_handle_packet_full_chain_emits_pcm_for_known_user():
    emitted = []
    c = DAVEVoiceClient(
        server_id=1,
        channel_id=2,
        user_id=3,
        session_id="s",
        token="t",
        endpoint="e",
        on_pcm=lambda uid, pcm: emitted.append((uid, pcm)),
    )
    c.transport = FakeTransport()
    c.mls = FakeMLS()
    c.opus = FakeOpus()
    c.ssrc_to_user = {0x01020304: 77}

    header = struct.pack(">BBHII", 0x80, 0x78, 1, 2, 0x01020304)
    nonce4 = struct.pack(">I", 9)
    packet = header + b"frame-bytes" + nonce4

    c._handle_packet(packet)

    assert len(emitted) == 1
    uid, pcm = emitted[0]
    assert uid == 77
    # transport -> b"CIPHER->frame-bytes", decryptor -> b"OPUS:CIPHER->frame-bytes", opus -> PCM(...)
    assert pcm == b"PCM(OPUS:CIPHER->frame-bytes)"


def test_handle_packet_unknown_ssrc_is_dropped():
    emitted = []
    c = DAVEVoiceClient(
        server_id=1,
        channel_id=2,
        user_id=3,
        session_id="s",
        token="t",
        endpoint="e",
        on_pcm=lambda uid, pcm: emitted.append((uid, pcm)),
    )
    c.transport = FakeTransport()
    c.mls = FakeMLS()
    c.opus = FakeOpus()
    c.ssrc_to_user = {}
    header = struct.pack(">BBHII", 0x80, 0x78, 1, 2, 0xDEADBEEF)
    c._handle_packet(header + b"x" + struct.pack(">I", 1))
    assert emitted == []


def test_handle_packet_none_plaintext_is_dropped():
    emitted = []
    c = DAVEVoiceClient(
        server_id=1,
        channel_id=2,
        user_id=3,
        session_id="s",
        token="t",
        endpoint="e",
        on_pcm=lambda uid, pcm: emitted.append((uid, pcm)),
    )

    class NoneDecryptor:
        def decrypt(self, mt, frame):
            return None

    class NoneMLS:
        def decryptor_for(self, ssrc):
            return NoneDecryptor()

    c.transport = FakeTransport()
    c.mls = NoneMLS()
    c.opus = FakeOpus()
    c.ssrc_to_user = {0x01020304: 77}
    header = struct.pack(">BBHII", 0x80, 0x78, 1, 2, 0x01020304)
    c._handle_packet(header + b"x" + struct.pack(">I", 1))
    assert emitted == []


# ---- new tests for Finding #2 and #3 ----


def _make_client(dave_version=0):
    """Return a DAVEVoiceClient with fakes wired in."""
    c = DAVEVoiceClient(
        server_id=1,
        channel_id=2,
        user_id=3,
        session_id="s",
        token="t",
        endpoint="e",
        on_pcm=lambda uid, pcm: None,
    )
    c.mls = FakeMLS()
    c.opus = FakeOpus()
    c.dave_version = dave_version
    return c


def test_on_speaking_adds_recognized_user():
    c = _make_client(dave_version=1)
    c._on_speaking(77, 1234)
    assert "77" in c.mls.recognized_user_ids
    assert c.ssrc_to_user[1234] == 77


def test_on_clients_connect_adds_all():
    c = _make_client(dave_version=1)
    c._on_clients_connect([10, 20])
    assert "10" in c.mls.recognized_user_ids
    assert "20" in c.mls.recognized_user_ids


def test_on_client_disconnect_removes_user_and_frees_ssrc():
    c = _make_client(dave_version=1)
    c.mls.recognized_user_ids = {"10"}
    c.ssrc_to_user = {555: 10}
    # Ensure a decryptor exists for ssrc 555
    fake_dec = FakeDecryptor()
    c.mls.decryptors[555] = fake_dec

    c._on_client_disconnect(10)

    assert "10" not in c.mls.recognized_user_ids
    assert 555 not in c.ssrc_to_user
    assert 555 not in c.mls.decryptors
    assert 555 in c.opus._resets


def test_handle_packet_drops_when_transport_none():
    emitted = []
    c = DAVEVoiceClient(
        server_id=1,
        channel_id=2,
        user_id=3,
        session_id="s",
        token="t",
        endpoint="e",
        on_pcm=lambda uid, pcm: emitted.append((uid, pcm)),
    )
    # transport is None by default; send a well-formed-ish packet
    header = struct.pack(">BBHII", 0x80, 0x78, 1, 2, 0x01020304)
    c._handle_packet(header + b"frame" + struct.pack(">I", 9))
    assert emitted == []


def test_handle_packet_strips_rtp_extension_body_before_dave_decrypt():
    """Extended packet: preamble is AAD; the extension body rides in the transport
    ciphertext and must be stripped BEFORE the DAVE decrypt (else it is counted as
    DAVE ciphertext and the AES-GCM tag fails)."""
    emitted = []
    c = DAVEVoiceClient(
        server_id=1,
        channel_id=2,
        user_id=3,
        session_id="s",
        token="t",
        endpoint="e",
        on_pcm=lambda uid, pcm: emitted.append((uid, pcm)),
    )

    class IdentityTransport:
        # echo the ciphertext so we can track exact byte boundaries
        def decrypt(self, header, ciphertext, nonce):
            return ciphertext

    class MarkingDecryptor:
        # marks its input so the test proves the extension body was already gone
        def decrypt(self, mt, frame):
            return b"D:" + frame

    class MarkingMLS:
        def decryptor_for(self, ssrc):
            return MarkingDecryptor()

    c.transport = IdentityTransport()
    c.mls = MarkingMLS()
    c.opus = FakeOpus()
    c.ssrc_to_user = {0x01020304: 77}

    header = struct.pack(">BBHII", 0x90, 0x78, 1, 2, 0x01020304)  # 0x90: extension bit set
    preamble = b"\xbe\xde" + struct.pack(">H", 1)  # profile + length = 1 word
    ext_body = b"EXTB"  # 1 word = 4 bytes, must be stripped BEFORE the DAVE decrypt
    opus = b"OPUS-AUDIO"
    nonce4 = struct.pack(">I", 9)
    packet = header + preamble + ext_body + opus + nonce4

    c._handle_packet(packet)

    assert len(emitted) == 1
    uid, pcm = emitted[0]
    assert uid == 77
    # the DAVE decryptor saw the opus WITHOUT the extension body in front of it
    assert pcm == b"PCM(D:OPUS-AUDIO)"
