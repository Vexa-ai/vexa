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
- **`ingest` / `consume_segments` / `reclaim_segments`** — `ingest.py`. `transcription_segments`
  stream → `store` → publish `tc:meeting:{id}:mutable` and append to the per-meeting feed
  `tc:meeting:{id}` (transcript.v1 `FeedEntry`). The stream-facing paths admit only an entry signed by
  a MeetingToken for the meeting it writes for (`auth` + `sig`, transcript.v1 `StreamEntry`); anything
  else is acknowledged and dropped. No background loop — the caller drives it (eval `tick`).
- **`segment_entry.py`** — transcript.v1's signer, vendored byte for byte from the contract (fact
  `segment-entry-signer`); the package's front door exports it as `signed_entry` for the tools that publish to the stream.
- **`erased_feed_sweep.py`** — the one-time operator sweep
  (`python -m meeting_api.collector.erased_feed_sweep [--dry-run]`): erases the Redis transcript keys
  of every meeting whose transcript was deleted, the same keys the delete route erases.
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
