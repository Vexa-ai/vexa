# Agent Test: Load Test Analysis

## Prerequisites
- Services running: target service(s) under test (Docker)
- Load test results: run deterministic load tests first to generate data
- Setup: `python tests/load/transcription_service.py --mode single` (or other modes)

## Tests

### Test 1: Single Request Baseline
**Goal:** Establish baseline latency for a single transcription request.
**Setup:** Run `python tests/load/transcription_service.py --mode single` and capture the JSON output.
**Verify:** Latency is within acceptable range. Known baseline: ~14.4s average for a single client.
**Evidence:** Capture p50, p95, p99 latency values from the JSON report.
**Pass criteria:** p50 latency under 20s. p99 under 30s. Zero errors.

### Test 2: Concurrent Throughput Curve
**Goal:** Determine maximum concurrent capacity before degradation.
**Setup:** Run with increasing VUs: `--mode concurrent --vus 1`, `--vus 5`, `--vus 10`.
**Verify:** Plot throughput (requests/sec) vs VUs. Identify the inflection point where latency spikes or error rate increases.
**Evidence:** Capture JSON results for each VU count. Note the point where error rate exceeds 5% or latency doubles.
**Pass criteria:** System handles at least 5 concurrent requests with <50% latency degradation. Known issue: 10 concurrent shows catastrophic degradation (15% coverage).

### Test 3: Memory Leak Detection
**Goal:** Verify no memory leaks during sustained load.
**Setup:** Run `python tests/load/transcription_service.py --mode memory --vus 2` for at least 10 minutes.
**Verify:** Compare memory usage at start vs end. Check docker stats snapshots in the JSON output.
**Evidence:** Capture memory readings at regular intervals from the JSON report.
**Pass criteria:** Memory growth under 15% after initial warmup period. No OOM events.

### Test 4: Resource Efficiency
**Goal:** Evaluate CPU and memory efficiency per request.
**Setup:** Analyze docker stats captured during load tests.
**Verify:** CPU and memory per request is proportional to load. No resource accumulation between requests.
**Evidence:** Calculate resources-per-request from the docker stats data.
**Pass criteria:** Resources return to baseline between request batches. CPU per request is consistent (no degradation over time).
