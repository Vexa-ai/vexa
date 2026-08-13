# discord_bot — the Discord platform-lane bot (#875)

| Module | Concern |
|---|---|
| `dave_voice/` | the ported DAVE/E2EE voice-receive stack (opcodes, gateway v8, MLS, transport decrypt, RTP, UDP, opus, the py-cord `VoiceProtocol` shim) |
| `contracts.py` | sealed-schema loaders/validators, loaded **by path** (invocation/transcript/lifecycle/acts) |
| `invocation.py` | parse + validate `VEXA_BOT_CONFIG` at boot (fail-fast) |
| `segmenter.py` | the silence-gap `PcmBuffer` (per-speaker utterance boundaries) |
| `audio.py` | 48 kHz stereo PCM → 16 kHz mono WAV |
| `adapters/` | transcript.v1 (redis) / lifecycle.v1 (HTTP) / acts.v1 (redis) / transcription (HTTP) egress-ingress ports |
| `session.py` | `MeetingSession` — the orchestration core, offline-testable over injected ports |
| `bot.py` | the composition root: real py-cord + `DAVEVoiceClient` + redis + httpx |
| `__main__.py` | `python -m discord_bot` — what the `discord-bot` runtime profile execs |

See the service root `README.md` for the full picture (wire contracts, deployment config, leave-on-empty-channel).
