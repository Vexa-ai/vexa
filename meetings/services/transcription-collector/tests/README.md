# tests — the collector's offline evals

Discovered by `gate:python` (pyproject + tests/) and `gate:health` (FastAPI app → needs
`test_health.py`). All OFFLINE — no docker, no DB, no real redis.

| File | Proves |
|---|---|
| `test_health.py` | GET `/health` → 200 `{status:"ok", service:"transcription-collector"}` (gate:health) |
| `test_collector_api.py` | the 3 HTTP routes; responses validated against the SEALED `api.v1` components (BY PATH); ownership + fail-closed 401 negatives |
| `test_ingest.py` | the ingestion eval: stream → store → publish `tc:meeting:{id}:mutable`, driven by fakeredis (explicit `ingest` / `consume_segments` calls, no loop) |
| `contracts.py` | loads the sealed `api.v1` schema (in the gateway lane) BY PATH and validates a payload against `#/components/schemas/<Shape>` — the same oracle the gateway conformance trusts |

Run: `uv run pytest -q`.
