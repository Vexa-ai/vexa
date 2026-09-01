# tests — discord-bot (offline, `gate:python`)

`uv run pytest -q`. No live Discord, no network, no docker. `dave.py` and `py-cord[voice]` are real pinned dependencies — the ported `dave_voice/` suite (`test_opcodes.py`, `test_rtp.py`, `test_transport.py`, `test_ip_discovery.py`, `test_udp_receiver.py`, `test_opus_decode.py`, `test_mls.py`, `test_voice_ws_routing.py`, `test_voice_client_decode.py`, `test_discord_protocol.py`) exercises the real packages with MLS crypto internals faked at the seam (`test_mls.py` monkeypatches `dave.Session`/`Decryptor`/`RejectType`), ported verbatim from [rennf93/discord-vexa-bridge](https://github.com/rennf93/discord-vexa-bridge)'s own proven suite.

| File | Proves |
|---|---|
| `test_invocation.py` | `VEXA_BOT_CONFIG` parsing/validation against the sealed invocation.v1 schema; the `discord` platform is spawnable; the legacy `BOT_CONFIG` alias fallback |
| `test_segmenter.py` | the silence-gap `PcmBuffer`: per-user buffering, silence-triggered flush, independent multi-user segmentation |
| `test_audio.py` | 48 kHz stereo → 16 kHz mono WAV shape |
| `test_adapters.py` | transcript.v1 emission (stream + pub/sub envelopes), lifecycle.v1 HTTP callbacks (retry/backoff/never-raises), acts.v1 ingress (dispatch + ignore-unknown + a raising handler doesn't kill the subscription) |
| `test_session.py` | `MeetingSession`: lifecycle.v1 state transitions, segment flush → transcribe → transcript.v1 emission (incl. dropped blips / empty-VAD / worker-unreachable), acts.v1 `leave`/`reconfigure` handling, leave-on-empty-channel (`left_alone`) timing |
