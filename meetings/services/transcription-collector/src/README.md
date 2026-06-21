# src — the transcription-collector package

The Python source for `transcription_collector` (a uv package; `pyproject.toml` puts `src` on the
path). The front door is `transcription_collector/__init__.py` (`__all__`); everything else is
internal.

| Module | Role |
|---|---|
| `app.py` | `create_app(store, redis, ...)` — the 3 HTTP routes + `/health` over the injected ports |
| `ingest.py` | `ingest` / `consume_segments` — the segment-ingestion unit (stream → store → publish `:mutable`) |
| `ports.py` | the Protocols: `TranscriptStore`, `RedisBus`, `PubSub` |
| `adapters.py` | production adapters (SQLAlchemy-async store + redis bus) + `build_production_app` |
| `models.py` | self-contained SQLAlchemy mirror of the `meetings` / `transcriptions` tables (lazy-imported by `adapters`) |
| `fakes.py` | `InMemoryTranscriptStore` / `FakeRedisBus` — offline drivers for the eval + gateway conformance |
| `obs.py` | the lane's `logevent.v1` trace emitter (`TraceMiddleware`, `log_event`) |
