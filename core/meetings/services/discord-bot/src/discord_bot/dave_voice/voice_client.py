# Copyright 2026 Vexa
# SPDX-License-Identifier: Apache-2.0
#
# Ported from https://github.com/rennf93/discord-vexa-bridge (dave_voice/voice_client.py) under a
# written Apache-2.0 license grant from the original author (Renzo Franceschini) for in-tree
# contribution to Vexa (vexa-ai/vexa#875); upstream discord-vexa-bridge itself remains
# AGPL-3.0-or-later — this grant covers only this in-tree copy.
"""Top-level DAVE voice receive client: assembles WS + UDP + transport + MLS + opus."""

import asyncio
import socket

import dave

from discord_bot.dave_voice import ip_discovery
from discord_bot.dave_voice.mls import MLSManager
from discord_bot.dave_voice.opus_decode import OpusDecoders
from discord_bot.dave_voice.rtp import HEADER_LEN, parse_rtp_header
from discord_bot.dave_voice.transport import SUPPORTED_MODES, TransportCrypto
from discord_bot.dave_voice.udp_receiver import VoiceUDPProtocol
from discord_bot.dave_voice.voice_ws import VoiceGateway

_EXT_FLAG = 0x10  # version_flags bit indicating an RTP header extension


class DAVEVoiceClient:
    def __init__(self, *, server_id, channel_id, user_id, session_id, token, endpoint, on_pcm):
        self.server_id = server_id
        self.channel_id = channel_id
        self.user_id = user_id
        self.session_id = session_id
        self.token = token
        self.endpoint = endpoint.replace("wss://", "").replace(":443", "")
        self.on_pcm = on_pcm

        self.mls = MLSManager(self_user_id=user_id)
        self.mls.set_group_id(channel_id)
        self.opus = OpusDecoders()
        self.transport: TransportCrypto | None = None
        self.ssrc_to_user: dict[int, int] = {}
        self.dave_version = 0

        self._ssrc = None
        self._server_ip = None
        self._server_port = None
        self._modes = []
        self._udp_transport = None
        self._sock = None
        self._gw = None
        self._ready_evt = asyncio.Event()
        self._sd_evt = asyncio.Event()

    # ---- callbacks from the gateway ----
    def _on_ready(self, d):
        self._ssrc = d["ssrc"]
        self._server_ip = d["ip"]
        self._server_port = d["port"]
        self._modes = d["modes"]
        self._ready_evt.set()

    def _on_session_description(self, d):
        self.dave_version = d.get("dave_protocol_version", 0) or 0
        self.mls.set_version(self.dave_version if self.dave_version else 1)
        secret_key = bytes(d["secret_key"])
        mode = d.get("mode") or self._chosen_mode
        self.transport = TransportCrypto(mode, secret_key)
        self._sd_evt.set()

    def _on_speaking(self, user_id, ssrc):
        self.mls.recognized_user_ids.add(str(user_id))
        self.ssrc_to_user[ssrc] = user_id
        if self.dave_version >= 1:
            self.mls.refresh_ratchets(self.ssrc_to_user)

    def _on_clients_connect(self, user_ids: list[int]) -> None:
        for u in user_ids:
            self.mls.recognized_user_ids.add(str(u))
        if self.dave_version >= 1:
            self.mls.refresh_ratchets(self.ssrc_to_user)

    def _on_client_disconnect(self, user_id: int) -> None:
        self.mls.recognized_user_ids.discard(str(user_id))
        dead_ssrcs = [ssrc for ssrc, uid in self.ssrc_to_user.items() if uid == user_id]
        for ssrc in dead_ssrcs:
            del self.ssrc_to_user[ssrc]
            self.mls.decryptors.pop(ssrc, None)
            self.opus.reset(ssrc)

    def _on_execute(self, transition_id):
        if self.dave_version >= 1:
            self.mls.refresh_ratchets(self.ssrc_to_user)

    def _on_mls_change(self):
        # Fired after a commit/welcome advances the MLS epoch (incl. tid=0 immediate
        # transitions that send no op22). Wire each established sender's key ratchet
        # into its Decryptor now that the group has exported keys.
        if self.dave_version >= 1:
            self.mls.refresh_ratchets(self.ssrc_to_user)

    def _on_prepare_passthrough(self):
        for ssrc in list(self.mls.decryptors):
            self.mls.decryptor_for(ssrc).transition_to_passthrough_mode(True, 0.0)

    # ---- receive path (unit-tested core) ----
    def _handle_packet(self, data: bytes) -> None:
        if self.transport is None:
            return
        if len(data) < HEADER_LEN + 4:
            return
        pkt = parse_rtp_header(data)
        user_id = self.ssrc_to_user.get(pkt.ssrc)
        if user_id is None:
            return

        # rtpsize framing (matches discord/voice/packets/rtp.py adjust_rtpsize):
        # skip CSRCs; the trailing 4 bytes are the nonce; for an extended packet the
        # 4-byte extension preamble is authenticated (AAD) while the extension body
        # stays in the ciphertext and is stripped after transport + DAVE decryption.
        cc = pkt.version_flags & 0x0F
        extended = bool(pkt.version_flags & _EXT_FLAG)
        header = data[:HEADER_LEN]
        payload = data[HEADER_LEN + cc * 4 :]
        if len(payload) < 4:
            return
        nonce = payload[-4:]
        ext_body_len = 0
        if extended:
            header = header + payload[:4]
            ext_body_len = int.from_bytes(payload[2:4], "big") * 4
            ciphertext = payload[4:-4]
        else:
            ciphertext = payload[:-4]

        try:
            transport_plain = self.transport.decrypt(header, ciphertext, nonce)
        except Exception:
            return  # bad/non-media packet — drop without crashing the receive loop
        # The RTP extension is added at the RTP layer AFTER the sender DAVE-encrypts,
        # so its body sits in front of the DAVE frame in the transport plaintext.
        # Strip it BEFORE the DAVE decrypt or it is counted as ciphertext and the
        # AES-GCM tag fails ("Failed to finalize decryption").
        dave_frame = transport_plain[ext_body_len:] if ext_body_len else transport_plain
        frame = self.mls.decryptor_for(pkt.ssrc).decrypt(dave.MediaType.audio, dave_frame)
        if not frame:
            return
        pcm = self.opus.decode(pkt.ssrc, frame)
        if pcm:
            self.on_pcm(user_id, pcm)

    @property
    def _chosen_mode(self):
        for m in SUPPORTED_MODES:
            if m in self._modes:
                return m
        raise RuntimeError(f"no supported transport mode in {self._modes}")

    # ---- lifecycle ----
    async def start(self):
        self._gw = VoiceGateway(
            endpoint=self.endpoint,
            server_id=self.server_id,
            user_id=self.user_id,
            session_id=self.session_id,
            token=self.token,
            mls=self.mls,
            on_ready=self._on_ready,
            on_session_description=self._on_session_description,
            on_speaking=self._on_speaking,
            on_execute=self._on_execute,
            on_prepare_passthrough=self._on_prepare_passthrough,
            on_clients_connect=self._on_clients_connect,
            on_client_disconnect=self._on_client_disconnect,
            on_mls_change=self._on_mls_change,
        )
        await self._gw.connect()
        await self._ready_evt.wait()

        # IP discovery over a UDP socket, then keep it for receive.
        loop = asyncio.get_running_loop()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._sock.connect((self._server_ip, self._server_port))
        await loop.sock_sendall(self._sock, ip_discovery.build_request(self._ssrc))
        resp = await loop.sock_recv(self._sock, 74)
        my_ip, my_port = ip_discovery.parse_response(resp)

        await self._gw.send_json(
            1,
            {  # Select Protocol
                "protocol": "udp",
                "data": {"address": my_ip, "port": my_port, "mode": self._chosen_mode},
            },
        )
        await self._sd_evt.wait()

        # Hand the connected socket to an asyncio datagram endpoint for receive.
        self._udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: VoiceUDPProtocol(self._handle_packet),
            sock=self._sock,
        )

    async def stop(self):
        if self._udp_transport:
            self._udp_transport.close()
        if self._gw:
            await self._gw.close()
