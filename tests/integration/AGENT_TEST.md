# Agent Test: Cross-Service Integration

## Prerequisites
- Services running: all services via `docker compose up -d`
- Environment: .env fully configured
- Database: migrated (`make migrate-or-init`)
- Setup: Ensure all services report healthy via `docker compose ps`

## Tests

### Test 1: Transcription Chain (WhisperLive -> Transcription Service)
**Goal:** Verify audio flows from WebSocket input through WhisperLive to the transcription service and produces a stored transcript.
**Setup:** Run `python tests/integration/test_transcription_chain.py` to execute the deterministic test first.
**Verify:** Audio sent via WebSocket appears as a completed transcription in the database. The transcript text is coherent and matches the input audio.
**Evidence:** Capture the transcript from the database. Compare with expected reference text if available.
**Pass criteria:** End-to-end latency under 30 seconds for a 10-second clip. Transcript is recognizable. No data lost between services.

### Test 2: Token Scoping Across Services
**Goal:** Verify token scope enforcement works consistently across api-gateway, admin-api, and bot-manager.
**Setup:** Run `pytest tests/test_token_scoping_integration.py` for deterministic checks first.
**Verify:** A bot token (vxa_bot_) cannot access user endpoints. A user token (vxa_user_) cannot access admin endpoints. Verify at the gateway level and at each backend independently.
**Evidence:** Capture a matrix showing each token type vs each endpoint category with pass/fail results.
**Pass criteria:** 100% correct enforcement. No token type can access endpoints outside its scope.

### Test 3: Webhook Delivery Reliability
**Goal:** Verify webhooks are delivered reliably with retry logic.
**Setup:** Run `pytest tests/test_webhook_delivery.py` for deterministic checks first. Then configure a webhook endpoint (use a local HTTP server) and trigger events.
**Verify:** Webhooks are delivered within expected timeframe. Failed deliveries are retried. Retry queue in Redis shows correct behavior.
**Evidence:** Capture webhook delivery timestamps, retry counts, and Redis queue depth over time.
**Pass criteria:** All webhooks eventually delivered (within retry window). No lost events. Redis queue drains to zero after successful delivery.

### Test 4: Service Recovery
**Goal:** Verify the system recovers gracefully when a service restarts.
**Setup:** With all services running, restart one service at a time (e.g., `docker compose restart api-gateway`).
**Verify:** Other services continue operating. The restarted service reconnects to its dependencies. No data loss during restart.
**Evidence:** Capture logs from each service during the restart window. Check for reconnection messages and error counts.
**Pass criteria:** Service available within 30 seconds of restart. No cascading failures. Zero data loss.
