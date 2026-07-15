# collector — the folded-in transcript backend

The transcript read-side + segment-ingestion the gateway proxies `/transcripts` + `/meetings` +
`/ws/authorize-subscribe` to. **Relocated VERBATIM** from the standalone `transcription_collector`
service into `meeting_api.collector` (P2 unification) — the same shipped code, now a front-doored
sub-package of the one meeting-api modular monolith. Mounted by `meeting_api.app.create_app`
alongside lifecycle / bot_spawn / recordings. Import direction is one-way: the gateway conformance
harness imports this sub-package to drive the shipped collector; this package imports nothing from
conformance.

- **`create_app(store, redis, ...)`** / **`build_router(store, redis, ...)`** — `app.py`. GET
  `/transcripts/{platform}/{native_meeting_id}` (api.v1 `TranscriptionResponse`), GET `/meetings`
  (api.v1 `MeetingListResponse`), POST `/ws/authorize-subscribe` (the gateway `/ws` authorizer hop).
  `build_router` is the mountable `APIRouter` the unified app composes in (one app, one `/health`);
  `create_app` is the standalone app the conformance harness + this module's tests still drive.
  Identity arrives as the gateway-injected `x-user-id` header (missing → 401).
- **`ingest` / `consume_segments`** — `ingest.py`. `transcription_segments` stream → `store` →
  publish `tc:meeting:{id}:mutable`. No background loop — the caller drives it (eval `tick`). The
  production store takes the shared meeting-write barrier around each Redis segment mutation and
  each PostgreSQL flush. Once ZAKI capture withdrawal is durable, a later segment is dropped without
  persistence or live publication, and a buffered flush purges its Redis hash instead of writing or
  retrying transcript PII. The always-on consumer loop is a P3 seam.
- **`ports.py`** — `TranscriptStore`, `RedisBus`, `PubSub` (Protocols; real adapters + fakes both
  satisfy them structurally).
- **`adapters.py`** — the real SQLAlchemy-async + redis wiring (lazy imports).
- **`models.py`** — re-exports the shared SQLAlchemy mirror from `meeting_api.sessions.models` (ONE
  `Base` per monolith).
- **`fakes.py`** — `InMemoryTranscriptStore` + `FakeRedisBus` (offline).
- **`obs.py`** — `logevent.v1` trace emitter, bound to `service="transcription-collector"` (the
  collector hop identity is preserved); reads the gateway-forwarded `X-Trace-Id` so this hop's logs
  join the same trace.

Tests (relocated into meeting-api's suite): `../../../../tests/test_collector_api.py`,
`test_ingest.py`, `test_collector_health.py` (+ the `collector_contracts.py` api.v1 oracle).
