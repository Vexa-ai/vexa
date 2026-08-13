# dave_voice — the DAVE/E2EE voice-receive stack

Ported near-verbatim from [rennf93/discord-vexa-bridge](https://github.com/rennf93/discord-vexa-bridge)'s `dave_voice/` (proven in production against real Discord voice calls) under a written Apache-2.0 license grant from its author (Renzo Franceschini) for this in-tree contribution to Vexa (vexa-ai/vexa#875) — each file carries its own Apache-2.0 header + a provenance line naming the exact origin file. The upstream repo itself remains AGPL-3.0-or-later; the grant covers only these in-tree copies. Import paths were updated from the bare `dave_voice.*` package to `discord_bot.dave_voice.*`; the logic is otherwise untouched, comments included — they document a tricky, Discord-controlled protocol.

Since March 2026 Discord requires the DAVE protocol (MLS/RFC-9420 end-to-end encryption) on all voice — off-the-shelf libraries can't decrypt it. This is the **receive** half: voice-gateway v8 handshake, MLS group join, per-sender frame decryption.

| File | Concern |
|---|---|
| `opcodes.py` | voice gateway v8 opcodes + the binary frame (de)framing DAVE messages ride on |
| `voice_ws.py` | the gateway v8 connection lifecycle: heartbeat + `seq_ack`, opcode dispatch |
| `mls.py` | MLS group join + per-sender key-ratchet orchestration, over `dave.py`'s `Session`/`Decryptor` |
| `transport.py` | rtpsize transport decrypt (AES-256-GCM / XChaCha20-Poly1305) |
| `rtp.py` | minimal RTP header parsing |
| `udp_receiver.py` | the asyncio UDP receive endpoint |
| `ip_discovery.py` | Discord's UDP IP-discovery handshake (finds our public ip/port for Select Protocol) |
| `opus_decode.py` | per-SSRC Opus → 48 kHz PCM (`fec=False` — the binding defaults to decoding in-band FEC, a stale redundant copy of the PREVIOUS frame, not the current one) |
| `voice_client.py` | `DAVEVoiceClient` — assembles WS + UDP + transport + MLS + opus into one receive pipeline; the clean boundary out is the `on_pcm(user_id, pcm)` callback |
| `discord_protocol.py` | `DAVEVoiceProtocol`, a py-cord `VoiceProtocol` shim that captures the voice handshake credentials (py-cord does not dispatch `VOICE_SERVER_UPDATE`/`VOICE_STATE_UPDATE` as client-level events — only to a *registered* `VoiceProtocol`) |

## MLS/RFC-9420 crypto is not hand-rolled

`mls.py` is pure routing/state logic over `dave.py` 0.1.2, a prebuilt MIT-licensed PyPI wheel by Disnake Development (a pinned `pyproject.toml` dependency, an ordinary third-party package like `py-cord` — never vendored or compiled in-tree). All RFC-9420 group-state math and frame crypto live inside that binding.
