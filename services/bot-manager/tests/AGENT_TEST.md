# Agent Test: Bot Manager

## Prerequisites
- Services running: bot-manager, postgres, redis (Docker)
- Environment: BOT_IMAGE_NAME set, ADMIN_API_TOKEN set
- Setup: `docker compose up -d bot-manager postgres redis`

## Tests

### Test 1: Bot Lifecycle
**Goal:** Verify a bot can be created, starts running, and can be stopped cleanly.
**Setup:** Use the admin API to create a bot instance: `curl -X POST http://localhost:8057/bots -H "Authorization: Bearer $ADMIN_API_TOKEN" -H "Content-Type: application/json" -d '{"meeting_url": "https://example.com/meeting"}'`
**Verify:** Bot container appears in `docker ps`. Bot status transitions: pending -> running -> stopped (on manual stop).
**Evidence:** Capture docker ps output showing bot container. Capture bot status from API at each lifecycle stage.
**Pass criteria:** Bot container starts within 30 seconds. Status transitions are correct. Stop command removes container cleanly.

### Test 2: Resource Usage Per Bot
**Goal:** Verify bot resource consumption matches expected baselines.
**Setup:** Launch a single bot and let it run for 2 minutes.
**Verify:** Check `docker stats` for the bot container. CPU should be around 250m, memory around 597Mi.
**Evidence:** Capture 3 docker stats snapshots at 30s intervals. Record CPU% and memory.
**Pass criteria:** CPU under 500m sustained. Memory under 800Mi. No memory growth trend.

### Test 3: Concurrent Bot Launch Race Condition
**Goal:** Verify that launching multiple bots simultaneously does not cause race conditions.
**Setup:** Run `pytest services/bot-manager/tests/test_concurrent_launch.py` first for deterministic check. Then manually launch 3 bots within 1 second via API.
**Verify:** All 3 bots start independently. No shared state corruption. Each bot gets a unique container.
**Evidence:** Capture docker ps showing 3 separate bot containers. Check database for 3 distinct bot records.
**Pass criteria:** All 3 bots running. No duplicate IDs. No error logs about lock contention or race conditions.

### Test 4: Bot Cleanup on Failure
**Goal:** Verify that failed bots are cleaned up properly (no orphaned containers or DB records).
**Setup:** Launch a bot with an invalid meeting URL that will cause it to fail.
**Verify:** Bot container is removed after failure. Database record shows failed status. No orphaned Docker resources.
**Evidence:** Check `docker ps -a` for stopped containers. Check bot status via API.
**Pass criteria:** No orphaned containers after 60 seconds. Bot status reflects the failure reason.
