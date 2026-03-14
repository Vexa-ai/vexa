# Agent Test: WhisperLive

## Prerequisites
- transcription-service running on localhost:8083
- WhisperLive test compose: `docker compose -f tests/docker-compose.test.yml up -d`
- Test audio: `services/transcription-service/tests/test_audio.wav`

## Tests

### Test 1: WebSocket Connection Stability
**Goal:** Verify WebSocket connections remain stable over a sustained period.
**Setup:** Connect a WebSocket client to `ws://localhost:19090/ws` and stream audio segments for 5 minutes.
**Verify:** Connection stays open without unexpected disconnects. Segments are acknowledged in order.
**Evidence:** Capture connection duration, number of segments sent vs acknowledged, any disconnect events.
**Pass criteria:** Zero unexpected disconnects over 5 minutes. All segments acknowledged within 2 seconds.

### Test 2: Segment Delivery Order
**Goal:** Verify audio segments are delivered to the transcription backend in the correct order.
**Setup:** Send 100 numbered audio segments over WebSocket.
**Verify:** Check transcription-service logs or output to confirm segments arrived in order.
**Evidence:** Capture segment sequence numbers from both client send log and server receive log.
**Pass criteria:** 100% of segments received in order. No duplicates, no gaps.

### Test 3: Concurrent Streams (Stress)
**Goal:** Verify WhisperLive handles multiple simultaneous WebSocket connections without cross-contamination.
**Setup:** Open 1, 5, then 10 concurrent WebSocket connections, each streaming Float32 audio.
**Verify:** All streams produce transcription output. No cross-contamination between streams. Resource usage stays reasonable.
**Evidence:** Per-stream segment counts, meeting_id verification, docker stats snapshots.
**Pass criteria:** All streams produce independent output. No mixed transcripts. Memory does not leak.
**Script:** `bash tests/test_stress.sh`

### Test 4: Graceful Disconnect Handling
**Goal:** Verify WhisperLive handles client disconnects cleanly.
**Setup:** Connect, stream for 30 seconds, then abruptly close the connection. Repeat with graceful close.
**Verify:** Server logs show clean resource cleanup. No zombie connections. Memory returns to baseline.
**Evidence:** Capture docker stats before and after disconnect. Check server logs for error traces.
**Pass criteria:** No error stack traces for graceful disconnects. Resources freed within 10 seconds of disconnect.

### Test 5: Security — Required Fields Enforcement
**Goal:** Verify connections without required identity fields are rejected.
**Setup:** Attempt WebSocket connections missing each of: `uid`, `platform`, `meeting_url`, `token`, `meeting_id`.
**Verify:** Server returns an ERROR status and closes the connection for each missing field.
**Evidence:** Capture server response for each missing-field attempt.
**Pass criteria:** All 5 missing-field cases are rejected with a clear error message. No connection proceeds without full identity.

### Test 6: Security — Authentication Assessment
**Goal:** Document the current authentication posture of the WebSocket endpoint.
**Setup:** Connect with arbitrary values for all required fields.
**Verify:** Document whether any token validation occurs, or if the server accepts any non-empty string.
**Evidence:** Capture whether connection succeeds with fake tokens.
**Pass criteria:** Documentation only — no hard pass/fail. Findings inform future security hardening.

### Test 7: Recovery — Transcription Service Failure
**Goal:** Verify WhisperLive handles transcription-service outage gracefully.
**Setup:** While streaming audio, stop transcription-service. Then restart it.
**Verify:** WhisperLive does not crash. It logs errors cleanly. After transcription-service recovers, WhisperLive resumes producing segments.
**Evidence:** WhisperLive container stays running. Error logs show HTTP failures (not stack traces). Post-recovery segments appear.
**Pass criteria:** Container survives outage. Clean error handling. Recovery without restart.

### Test 8: Docs Validation
**Goal:** Verify README accuracy against actual service behavior.
**Setup:** Cross-reference README claims with code and running service.
**Verify:** Port numbers, env vars, required fields, file layout all match reality.
**Pass criteria:** No stale or incorrect documentation.

### Test 9: Dev Experience
**Goal:** Verify a new developer can set up and test WhisperLive from the README alone.
**Setup:** Follow README instructions to start, test, and debug.
**Verify:** All commands work. Test scripts run. Health check responds. Debug WebSocket snippet works.
**Pass criteria:** Zero undocumented steps required.
