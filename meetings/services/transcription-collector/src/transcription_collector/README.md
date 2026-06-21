# transcription_collector — the importable package

The PRODUCTION transcript backend. Import direction is one-way: the gateway conformance harness
imports this package to drive the shipped collector; this package imports nothing from conformance.

- **`create_app(store, redis, ...)`** — `app.py`. GET `/transcripts/{platform}/{native_meeting_id}`
  (api.v1 `TranscriptionResponse`), GET `/meetings` (api.v1 `MeetingListResponse`), POST
  `/ws/authorize-subscribe` (the gateway `/ws` authorizer hop), `/health`. Identity arrives as the
  gateway-injected `x-user-id` header (missing → 401).
- **`ingest` / `consume_segments`** — `ingest.py`. `transcription_segments` stream → `store` →
  publish `tc:meeting:{id}:mutable`. No background loop — the caller drives it (eval `tick`).
- **`ports.py`** — `TranscriptStore`, `RedisBus`, `PubSub` (Protocols; real adapters + fakes both
  satisfy them structurally).
- **`adapters.py`** / **`models.py`** — the real SQLAlchemy-async + redis wiring (lazy imports).
- **`fakes.py`** — `InMemoryTranscriptStore` + `FakeRedisBus` (offline).
- **`obs.py`** — `logevent.v1` trace emitter, bound to `service="transcription-collector"`; reads
  the gateway-forwarded `X-Trace-Id` so this hop's logs join the same trace.
