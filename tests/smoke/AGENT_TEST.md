# Agent Test: Full Deployment Verification

## Prerequisites
- Services running: all services via `docker compose up -d`
- Environment: .env fully configured
- Database: migrated (`make migrate-or-init`)
- Setup: Run `bash tests/smoke/test_full_stack.sh` first for deterministic checks

## Tests

### Test 1: Service Health Dashboard
**Goal:** Verify all services are healthy and properly connected.
**Setup:** Run `docker compose ps` and `bash tests/smoke/test_full_stack.sh`.
**Verify:** All containers show "Up" status. All smoke test checks pass. No containers in restart loops.
**Evidence:** Capture docker compose ps output. Capture smoke test results.
**Pass criteria:** All services healthy. Zero failed smoke checks. No container restarts in the last 5 minutes.

### Test 2: End-to-End Meeting Flow
**Goal:** Verify the full meeting recording flow works from bot launch to transcript retrieval.
**Setup:** Create a user and API token via admin API. Launch a bot to a test meeting. Wait for transcript.
**Verify:** Bot starts and joins the meeting. Audio is captured and transcribed. Transcript is retrievable via API. Webhook is delivered (if configured).
**Evidence:** Capture API responses at each stage: token creation, bot launch, transcript retrieval.
**Pass criteria:** Complete flow succeeds without manual intervention. Transcript appears within 2 minutes of meeting end.

### Test 3: Error Recovery
**Goal:** Verify the system handles and recovers from errors gracefully.
**Setup:** Intentionally cause failures: (a) stop a backend service, (b) send malformed requests, (c) exhaust a resource.
**Verify:** Error responses are meaningful (not generic 500s). System recovers when the issue is resolved. No data corruption.
**Evidence:** Capture error responses and recovery behavior for each failure scenario.
**Pass criteria:** All errors produce actionable error messages. Recovery is automatic (no manual restart needed beyond the intentionally stopped service).

### Test 4: Deployment Configuration Audit
**Goal:** Verify the deployment matches expected configuration.
**Setup:** Review docker-compose.yml, .env, and running container configuration.
**Verify:** Port mappings match .env settings. Environment variables are correctly injected into containers. Volume mounts are correct. Network configuration allows inter-service communication.
**Evidence:** Capture `docker inspect` output for key containers. Compare with docker-compose.yml expectations.
**Pass criteria:** All configuration matches. No unexpected port exposures. No missing environment variables.
