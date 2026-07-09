# tests — autonomous contract suite

No GPU, no model download, no network. faster-whisper loads lazily (not at import) and
`TestClient(app)` runs no lifespan, so these pin the HTTP seam with `model` left unloaded or
swapped for a sentinel:

| File | Pins |
|---|---|
| `test_health.py` | `/health` → 503 unloaded / 200 loaded; `/` service info |
| `test_api.py` | `/v1/audio/transcriptions` token auth + multipart validation |

Real model inference is a GPU/integration concern — smoked by the deploy unit, not here.
