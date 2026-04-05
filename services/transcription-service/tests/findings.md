# Transcription Service Test Findings

## Run: 2026-03-16T19:31 UTC

**Target:** localhost:8085 (prod deployment from `/home/dima/prod/prod-transcription-service/`)
**Config:** 3 GPU workers, nginx LB, large-v3-turbo, cuda, int8

### Riskiest Thing

**Config drift between repo and running deployment.** The running service on port 8085 is deployed from `/home/dima/prod/prod-transcription-service/`, not from the repo at `/home/dima/dev/vexa/services/transcription-service/`. Key differences:

| Setting | Repo (dev) | Running (prod) |
|---------|-----------|----------------|
| Port | 8083 | 8085 |
| Workers in docker-compose | 2 uncommented, 1 commented | 3 active |
| Workers in nginx.conf | 2 uncommented, 1 commented | 3 active |
| API_TOKEN in .env | `your_secure_token_here` (placeholder) | Real token set |
| container_name | Not set (auto) | Explicitly set (`transcription-lb`, etc.) |

If someone runs `docker compose up` from the repo, they get a different setup than what's running. The repo README says port 8083, but the live service is 8085.

### Missing API Field

**`language_probability` is documented in README but not returned by the API.** The README response format section says the response includes `language_probability` (confidence 0.0-1.0). Actual response keys: `text`, `language`, `duration`, `segments`. This field is either not implemented or was removed.

### Test Results

| Test | Result | Detail |
|------|--------|--------|
| Health endpoint | PASS | 200, worker_id=3, model=large-v3-turbo, cuda, gpu_available=true |
| LB status | PASS | 200, "Active" |
| Transcription (single) | PASS | 200, 0.24s, coherent text, 14 words |
| Auth - no token | PASS | 401 "Invalid or missing API token" |
| Auth - wrong token | PASS | 401 "Invalid or missing API token" |
| Concurrent (3) | PASS | All 200, ~0.45s each |
| Concurrent (10) | PASS | 6x 200, 4x 503 — backpressure works |
| 503 response | PASS | Includes `retry-after: 1` header, fast fail (~12ms) |
| Load distribution | PASS | All 3 workers receive traffic (worker IDs 1, 2, 3 observed) |
| GPU memory | OK | 2 GPUs loaded (~20GB each), 2 GPUs idle (15MB each) |

### Surprising

1. **3 workers but only 2 GPUs loaded.** `nvidia-smi` shows 4 GPUs: 2 at ~20GB (model loaded), 2 at 15MB (idle). But 3 workers are running and all receive traffic. Worker 3 may be sharing a GPU with another worker, or the GPU reporting is aggregated differently. All 3 workers respond correctly regardless.

2. **test_hot.sh won't work on port 8085.** The script reads port from `docker-compose.yml` which says 8083. The running service is on 8085. The `--verify` mode would fail to connect or hit the wrong port.

3. **Rate limiting is very permissive.** nginx.conf has `rate=100r/s` with `burst=100` — effectively allows 200 requests/second/IP. The `limit_conn` is set to 50 concurrent connections/IP. These are far above what backpressure testing would trigger. The 503s during load test came from worker-level `FAIL_FAST_WHEN_BUSY`, not nginx rate limiting.

### Untested

- **CPU mode** (`docker-compose.cpu.yml`) — only GPU deployment running
- **Unit tests** (`pytest tests/`) — would require the service's Python env, not testing against running containers
- **Actual WhisperLive on port 8083** — connection refused on 8083. The prod deployment runs on 8085 instead. No separate WhisperLive service detected.
- **Repetition penalty / no_repeat_ngram_size** — test audio is too short and clean to trigger repetition loops
- **Large file upload** — only tested with the 5.48s test_audio.wav
- **Stress test script** (`test_stress.sh`) — not run to avoid impacting the production service
