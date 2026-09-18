# discord-bot — the Discord platform lane (#875)

A first-party meeting-bot kind, alongside the TS `bot` (google_meet/zoom/teams/jitsi): joins a Discord voice channel, receives **each speaker as a separate audio stream** (Discord's own per-user RTP — native diarization, no clustering needed), transcribes every utterance on Vexa's Whisper worker, and speaks the same sealed contracts every other meeting-bot kind speaks.

Python, not TypeScript, because the hard part is protocol work the rest of the tree has no code for: since March 2026 Discord enforces **E2EE on all voice via the DAVE protocol** (MLS/RFC-9420 group keys, per-sender frame decryption). This service ports the proven, pure-Python DAVE **receive** path from [rennf93/discord-vexa-bridge](https://github.com/rennf93/discord-vexa-bridge) (voice-gateway handshake, MLS group join, per-sender decrypt via the `dave.py` binding) under a written Apache-2.0 license grant from its author for this in-tree contribution — see the per-file provenance headers under `dave_voice/`.

## Why a second language for one platform

`core/runtime`'s `WorkloadSpec.profile` is opaque (`runtime.v1`) — the kernel already resolves `meeting-bot` and `agent` to different image/command pairs (`runtime_kernel/profiles.py`). A third profile, `discord-bot`, pointed at its own Python image, is a drop-in, not an architectural exception (`core/runtime/README.md`, `core/runtime/src/runtime_kernel/README.md`).

## Wire contracts (same seams every meeting-bot kind speaks)

| Contract | Direction | How |
|---|---|---|
| `invocation.v1` | in | one JSON env var `VEXA_BOT_CONFIG` (`discord_bot/invocation.py`) |
| `lifecycle.v1` | out | HTTP POST to `meetingApiCallbackUrl` (`discord_bot/adapters/lifecycle_http.py`) |
| `transcript.v1` | out | redis `XADD transcription_segments` + `PUBLISH tc:meeting:{id}:mutable` (`discord_bot/adapters/transcript_redis.py`) |
| `acts.v1` | in | redis `SUBSCRIBE bot_commands:meeting:{id}` (`discord_bot/adapters/acts_redis.py`) |

Every schema is loaded **by path** from `meetings/contracts/*.v1/*.schema.json` (`discord_bot/contracts.py`, P8) — this package can never drift from what it claims to speak. `discord` was added to the sealed `invocation.v1` Platform enum (a back-compatible, re-sealed widening) and to `stop_router.py`'s `_SUPPORTED_PLATFORMS`; it has no `_URL_TEMPLATES` entry (like zoom/jitsi, a caller must pass an explicit `meeting_url` — a Discord voice channel has no synthesizable URL from a bare id).

## Layout

- `discord_bot/dave_voice/` — the ported DAVE/E2EE receive stack (Apache-2.0 + provenance header per file; MLS/RFC-9420 crypto itself is NOT hand-rolled here — delegated to `dave.py`, a prebuilt MIT-licensed PyPI wheel, an ordinary third-party dependency like `py-cord`).
- `discord_bot/contracts.py` — sealed-schema loaders/validators (invocation/transcript/lifecycle/acts).
- `discord_bot/invocation.py` — parse + validate `VEXA_BOT_CONFIG` at boot (fail-fast).
- `discord_bot/segmenter.py` — the silence-gap `PcmBuffer`, ported as a pure data structure from the bridge's `bot.py`: Discord only sends voice packets while a user is transmitting, so a gap with no packets ends an utterance.
- `discord_bot/audio.py` — 48 kHz stereo PCM → 16 kHz mono WAV (the Whisper worker's input shape).
- `discord_bot/adapters/` — the egress/ingress ports over redis + HTTP (real IO, offline-testable via injected fakes — no `discord`/`dave` import needed to test these).
- `discord_bot/session.py` — `MeetingSession`, the orchestration core: wires the segmenter, transcription, transcript emission, lifecycle transitions, acts handling, and leave-on-empty-channel over injected ports. Knows nothing about `discord`/`dave` types — fully offline-testable.
- `discord_bot/bot.py` — the composition root: the real py-cord `Client`, `DAVEVoiceClient`, redis, and httpx, wired into one `MeetingSession`. NOT unit-tested directly (needs a live Discord gateway connection + a real bot token, which cannot be faked meaningfully offline) — it is the thin driver for the logic `session.py` carries, tested there.
- `discord_bot/__main__.py` — `python -m discord_bot`, what the `discord-bot` runtime profile execs.
- `mock/` — the offline instrument variant (`Dockerfile.mock`), following `services/bot/mock/`'s pattern: fakes only the Discord-specific port (voice join + PCM receive) and reuses every real adapter, so it proves the control plane (lifecycle/transcript/acts) with no live Discord/DAVE.

## Deployment-scoped config (not per-invocation)

Unlike every browser-based platform, a Discord "meeting" isn't reached with per-invocation credentials — it needs ONE registered Discord Application bot token, the SAME identity for every voice channel this deployment ever joins. That is deployment config, not an `invocation.v1` field: the runtime's `discord-bot` profile forwards `DISCORD_TOKEN` into every spawned workload's env (`runtime_kernel/profiles.py`, mirroring how `meeting-bot`'s tuning env is forwarded).

## Leave-on-empty-channel (`left_alone`)

Unlike the TS bot's audio-energy heuristic (no authoritative "who's here" signal in a browser tab), Discord's own channel roster IS authoritative — `MeetingSession.observe_channel_members` just counts non-bot members and starts a clock the instant it hits zero, tripping `left_alone` once `invocation.automaticLeave.everyoneLeftTimeout` has elapsed with nobody back.

## Tests

`uv run pytest -q` (`gate:python`) — fully offline. `dave.py` and `py-cord[voice]` are real pinned dependencies (both install cleanly from PyPI, incl. Linux/macOS wheels for `dave.py`) so the ported `dave_voice/` suite exercises the REAL packages with MLS crypto internals faked at the seam (`tests/test_mls.py` monkeypatches `dave.Session`/`Decryptor`/`RejectType`, mirroring the origin repo's own proven pattern) — never a live Discord gateway or network call.

## Python 3.11 pin

`dave_voice/opus_decode.py`'s downsample path and `discord_bot/audio.py` use the stdlib `audioop` module, removed in Python 3.13. Do not bump this service's runtime past 3.11.
