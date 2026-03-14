# Agent Test: Transcription Service

A standalone, general-purpose transcription service with OpenAI-compatible API. Tests verify it transcribes accurately, handles load, and maintains quality across languages.

## Prerequisites
- Service running: `cd services/transcription-service && docker compose up -d`
- Wait for "Model loaded successfully" in logs
- Port: read from `docker-compose.yml` (default 8083)
- Test audio: `tests/test_audio.wav`

## Tests

### Test 1: Docs flow validation
**Goal:** Follow the README exactly. Every documented command must work.
**Setup:** Stop the service. Follow README from "Run" section step by step.
**Verify:**
- `cp .env.example .env` works
- `docker compose up -d` starts without errors
- `docker compose logs -f` shows "Model loaded successfully"
- `curl http://localhost:PORT/health` returns healthy JSON
- The transcribe curl example from README returns a transcript
- `bash tests/test_hot.sh --verify` passes
- `pytest tests/ -v` passes
**Evidence:** Capture each command's output.
**Pass criteria:** Every documented command works. No undocumented steps needed.

### Test 2: Transcription quality
**Goal:** Verify the service produces accurate transcripts, not just 200 OK.
**Setup:** Run `bash tests/test_hot.sh --verify`
**Verify:**
- Transcript text is coherent (not hallucinated, not garbage)
- Language detection is correct
- Segment timestamps make sense (start < end, no gaps beyond 1s)
- Compression ratio < 1.8 (no repetition loops)
- avg_logprob > -1.0 (model is confident)
- no_speech_prob < 0.5 (actual speech detected)
**Evidence:** Full JSON response with segment-level metrics.
**Pass criteria:** Transcript matches the spoken content. Quality metrics within thresholds.

### Test 3: Quality gate (multi-language WER)
**Goal:** Verify transcription quality across languages using the quality evaluation framework.
**Setup:**
```bash
# Generate quality dataset (one-time)
cd tests && python3 -m quality.dataset_generate

# Run quality gate
TRANSCRIPTION_API_URL=http://localhost:PORT/v1/audio/transcriptions \
  pytest test_quality_gate.py -v
```
**Verify:** WER (Word Error Rate) per language is within acceptable thresholds.
**Evidence:** Quality report with per-language WER scores.
**Pass criteria:** English WER < 10%. Other languages WER < 20%. See `tests/README_QUALITY_TESTING.md` for details.

### Test 4: Stress test and capacity
**Goal:** Find the service's real limits — concurrency, audio sizes, breaking point.
**Setup:** `bash tests/test_stress.sh`
**Verify:**
- Latency by audio size (6s, 30s, 60s, 180s, 360s)
- Concurrency curve — at what point do requests fail?
- Per-worker VRAM usage (not total machine GPU memory)
- Throughput (RPS) at different concurrency levels
**Evidence:** Stress test output. Compare to baselines in `tests/load/results/README.md`.
**Pass criteria:** No regression > 20% from baselines. Failures only above documented capacity limit.

### Test 5: Error handling
**Goal:** Verify the service returns appropriate errors for invalid inputs.
**Setup:** Send requests with: (a) no file, (b) no model parameter, (c) wrong API token, (d) file too large.
**Verify:** Each returns appropriate HTTP error code (400, 401, 413, 422) with a descriptive message.
**Evidence:** Status codes and error messages for each case.
**Pass criteria:** All error cases return non-200 with meaningful descriptions. No stack traces leaked.

### Test 6: Resource usage
**Goal:** Verify no memory leaks under sustained load.
**Setup:** `bash tests/test_stress.sh --concurrency-only` (runs sustained concurrent requests)
**Verify:**
- Per-worker VRAM before and after: `nvidia-smi --query-compute-apps=pid,used_memory --format=csv`
- Container memory via `docker stats`
- Memory should stabilize, not grow unbounded
**Evidence:** VRAM and container memory snapshots at start, middle, end.
**Pass criteria:** VRAM growth < 100MB after warmup. Container memory growth < 10%. No OOM kills.
