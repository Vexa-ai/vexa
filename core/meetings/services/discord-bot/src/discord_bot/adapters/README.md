# adapters — egress/ingress ports over redis + HTTP

Each adapter is injected with a minimal fake surface (an `xadd`/`publish` pair, a `subscribe(channel, handler)`, an async `post(...)`) so it is offline-testable without a live redis or HTTP server — `tests/test_adapters.py` drives every one against fakes.

| File | Contract | Direction |
|---|---|---|
| `transcript_redis.py` | `transcript.v1` | out — `XADD transcription_segments` + `PUBLISH tc:meeting:{id}:mutable`, mirroring `services/bot/src/adapters/transcript-redis.ts`'s wire shape byte-for-byte |
| `lifecycle_http.py` | `lifecycle.v1` | out — POST to `meetingApiCallbackUrl`, bounded retry/backoff, never raises (a dropped status report must not crash the bot) |
| `acts_redis.py` | `acts.v1` | in — `SUBSCRIBE bot_commands:meeting:{id}`, off-contract messages ignored per the acts.v1 README |
| `transcribe_http.py` | — | out — OpenAI-compatible multipart POST to the invocation's `transcriptionServiceUrl` (Vexa's own Whisper worker) |
