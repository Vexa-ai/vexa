# transcription-collector — the transcript backend the gateway proxies to

The v0.12 PRODUCTION carve of `services/meeting-api/meeting_api/collector/` — the transcript
backend the gateway proxies `/transcripts` + `/meetings` + `/ws/authorize-subscribe` to. The
O-API-1 conformance suite drives **this shipped code** (not a port-fake): the gateway lane's
conformance harness routes those paths to `transcription_collector.create_app` with injected
fakes.

## Surface (the front door — `src/transcription_collector/__init__.py` `__all__`)

| Symbol | What |
|---|---|
| `create_app(store, redis, ...)` | the FastAPI collector — the 3 routes below + `/health` |
| `ingest(store, redis, message)` | process ONE `transcription_segments` stream message |
| `consume_segments(store, redis, ...)` | drain a stream batch (read → ingest → ack) |
| `TranscriptStore`, `RedisBus`, `PubSub` | the ports (Protocols) |

### HTTP routes (conform to the SEALED `api.v1` shapes, validated BY PATH)

- **GET `/transcripts/{platform}/{native_meeting_id}`** → `TranscriptionResponse` (404 if not owned).
- **GET `/meetings`** → `MeetingListResponse` (filters: `status` / `platform` / `limit` / `offset`).
- **POST `/ws/authorize-subscribe`** → the gateway `/ws` authorizer hop:
  `{meetings:[{platform, native_meeting_id}]}` + the gateway-injected `x-user-id` →
  `{authorized:[{platform, native_id, user_id, meeting_id}], errors:[]}` — the exact shape
  `gateway.ports.Authorizer.authorize_subscribe` consumes.
- **GET `/health`** → `{status:"ok", service:"transcription-collector"}` (gate:health).

The caller's identity arrives in the `x-user-id` header the gateway injects after it resolves
`x-api-key` (anti-spoofing: the gateway strips any client-supplied identity header first); the
collector trusts it (it sits behind the gateway). Missing → 401 fail-closed.

### Segment ingestion

`ingest` parses a stream message's `payload` JSON, appends each valid segment to the store, and
publishes one change-only update to **`tc:meeting:{id}:mutable`** — the pubsub channel the gateway
`/ws` fans in (`services/redis.md`). Driven explicitly in tests (no background loop), like the
runtime scheduler's `tick()`.

## Ports + adapters

`TranscriptStore` (read a transcript · list meetings · authorize subscribe · append a segment) and
`RedisBus` (read/ack the segments stream · publish `:mutable`). Real adapters
(`adapters.SqlAlchemyTranscriptStore` + `RedisStreamBus`, lazy-importing SQLAlchemy-async + redis)
and in-process fakes (`fakes.InMemoryTranscriptStore` + `FakeRedisBus` over fakeredis) both satisfy
them. Recordings/notes live in `meetings.data` JSONB — there is NO separate recordings table.

> No `greenlet` pin: SQLAlchemy-async is imported lazily (only `adapters` / `models` touch it, at
> prod runtime) — the gate venv's tests never import it, so the pin is unnecessary.

## Tests (gate:python · gate:health discover this package)

- `tests/test_health.py` — the liveness probe.
- `tests/test_collector_api.py` — the 3 routes, responses validated against the sealed `api.v1`
  components (loaded BY PATH via `tests/contracts.py`).
- `tests/test_ingest.py` — the ingestion eval, driven by fakeredis.

Run: `uv run pytest -q`.
