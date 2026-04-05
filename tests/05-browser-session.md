---
id: test/browser-session
type: validation
requires: [test/api-full]
produces: [SAVED_STATE, CDP_PORT]
validates: [remote-browser]
docs: [features/remote-browser/README.md, services/vexa-bot/README.md]
mode: hybrid
skill: /validate-browser-session
---

# Browser Session Validation

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

End-to-end validation for the browser session feature. Tests the full chain: API creates session → browser launches → user navigates → state saves to S3/MinIO → state loads on next session.

## Inputs

| Name | From | Default | Description |
|------|------|---------|-------------|
| API_TOKEN | test/api-full | — | API token with browser scope |
| GATEWAY_URL | test/infra-up | — | API gateway URL |
| ADMIN_TOKEN | test/infra-up | — | Admin API token |
| CONTAINER | test/infra-up | — | Container name (lite mode) |

## Prerequisites

- Running deployment (lite, compose, or production)
- MinIO or S3 configured (for state persistence)
- Redis running (for session commands)
- API token with `browser` scope
- Human available for login step

## Procedure

### 1. Create browser session via API

```bash
# Create a browser session — this spawns a container/process with Playwright
curl -sf -X POST http://localhost:8056/bots \
  -H "X-API-Key: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "browser_session",
    "bot_name": "Test Browser"
  }' | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'id' in d or 'meeting_id' in d, f'No session ID: {d}'
print(f'PASS: browser session created (id={d.get(\"id\", d.get(\"meeting_id\"))})')
print(f'Session token: {d.get(\"session_token\", \"check Redis\")}')
"
```

**FAIL if:** returns error. Common causes:
- `mode: browser_session` not recognized → schema outdated
- `meeting` profile missing → add to runtime-api/profiles.yaml
- Docker socket unavailable (compose) or process spawn failed (lite)

### 2. Verify browser is running

```bash
# Check container via runtime API
curl -sf http://localhost:8092/containers | python3 -c "
import sys, json
containers = json.load(sys.stdin)
running = [c for c in containers if c.get('status') == 'running']
print(f'Running containers: {len(running)}')
for c in running:
    print(f'  {c[\"name\"]}: profile={c.get(\"profile\", \"?\")} ports={c.get(\"ports\", {})}')
assert len(running) > 0, 'No running containers'
print('PASS: browser container running')
"
```

### 3. Navigate to a page (machine test)

Via Playwright CDP through the **gateway proxy** (from host, not docker exec):

```javascript
// SESSION_TOKEN comes from the bot creation response: data.session_token
const { chromium } = require('playwright');
const browser = await chromium.connectOverCDP('$GATEWAY_URL/b/$SESSION_TOKEN/cdp');
const page = browser.contexts()[0].pages()[0] || await browser.contexts()[0].newPage();
await page.goto('https://www.google.com');
console.log('Current URL:', page.url());
```

The gateway proxies the CDP WebSocket to the browser container's internal port 9222. All Playwright operations go through this proxy — no direct port mapping or docker exec needed.

**FAIL if:** CDP connection fails or page doesn't load.

### 4. Human login (requires human)

- Human opens dashboard (http://localhost:3011) and starts a browser session
- Human sees the browser via VNC iframe in the dashboard
- Human navigates to a service requiring login (e.g., Google Meet, GitHub)
- Human logs in
- Human confirms login succeeded (can see authenticated content)

### 5. Save state

```bash
# Trigger save via Redis command
redis-cli PUBLISH "browser_session:$CONTAINER_NAME" "save_storage"

# Wait for confirmation
# The bot publishes "save_storage:done" or "save_storage:error:..." back

# Or via bot save endpoint if available:
curl -sf -X POST "http://localhost:8080/b/$BOT_TOKEN/save" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('success'), f'Save failed: {d}'
print('PASS: browser state saved')
"
```

Verify data landed in MinIO/S3:

```bash
# Check MinIO for browser data
aws s3 ls "s3://$MINIO_BUCKET/userdata/$USER_ID/browser-data/" \
  --endpoint-url "$MINIO_ENDPOINT" 2>/dev/null | head -5

# Should see: Cookies, Preferences, Login Data, etc.
```

**FAIL if:** save command errors or no data in S3/MinIO.

### 6. Destroy and recreate session

```bash
# Stop the session
redis-cli PUBLISH "browser_session:$CONTAINER_NAME" '{"action": "leave"}'
# Or:
curl -sf -X DELETE "http://localhost:8056/bots/$BOT_ID" \
  -H "X-API-Key: $API_TOKEN"

# Wait for container to stop
sleep 5

# Create a NEW browser session (same user_id — should load saved state)
curl -sf -X POST http://localhost:8056/bots \
  -H "X-API-Key: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "browser_session",
    "bot_name": "Test Browser 2",
    "use_saved_userdata": true
  }'
```

### 7. Verify state loaded (requires human)

- Human opens VNC for the new session
- Browser should have the previous login state (cookies, local storage)
- Human navigates to the same service — should be logged in WITHOUT re-entering credentials

**FAIL if:** login state not restored. Check:
- S3 sync logs in bot output
- `syncBrowserDataFromS3` called on startup
- Browser data dir populated before Playwright launch

## Done state

All 7 steps pass. Browser session creates via API, user can navigate and log in, state saves to S3/MinIO, state loads on next session — login persists across sessions.

## Known gotchas

| Issue | Symptom | Fix |
|-------|---------|-----|
| MinIO not configured | Save succeeds silently (no-op) but data not persisted | Set MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET |
| `userdataS3Path` empty | s3Sync skips silently | Must be set in bot config (meeting-api passes it) |
| Cookie `SameSite`/`Secure` | Login works in session but cookies not saved by Chromium | Some sites require HTTPS context — use `--ignore-certificate-errors` |
| Stale lock files | Chromium refuses to start ("profile in use") | `cleanStaleLocks()` runs on startup — check it fires |
| Auth-essential-only upload | Full sync is slow (~minutes). Default uploads only auth files (~200KB, <2s) | If login not restoring, check which files are synced vs needed |

## Docs ownership

After this test runs, verify and update:

- **features/remote-browser/README.md**
  - DoD table: update Status, Evidence, Last checked for items #1 (browser session creates and container runs), #2 (CDP accessible through gateway proxy), #3 (login state persists across sessions via save + load)
  - Components table: verify `services/vexa-bot/core/src/browser-session.ts` path matches the actual browser-session entrypoint, `services/vexa-bot/core/src/s3-sync.ts` still handles auth-essential file sync, and `services/api-gateway/main.py` CDP proxy route exists
  - Architecture: verify the documented flow `POST /bots {mode: "browser_session"} -> container with Playwright + Xvfb + VNC + CDP` matches the actual container stack observed (check running processes inside the container)
  - Confidence score: recalculate after updating statuses

- **services/vexa-bot/README.md**
  - Browser Session Mode section: verify the documented CDP port (9222/9223), SSH port, and persistent browser profile path (`/tmp/browser-data`) match actual container configuration
  - Browser Session Mode: verify save triggers via Redis pub/sub on `browser_session:{container_name}` channel work as documented — the test exercises `save_storage` and `leave` commands
  - Browser Session Mode: verify workspace sync (git-based or MinIO-based) description matches actual behavior observed during state save/load
  - VNC Browser View section: verify Xvfb display `:99`, x11vnc on port 5900, and websockify on port 6080 match actual running services inside the bot container
  - Bot Capabilities: verify `mode: "browser_session"` bypasses meeting-join logic as documented
  - Configure table: verify `BOT_CONFIG` JSON fields (`mode`, `userdataS3Path`, `s3Endpoint`, `s3Bucket`, `s3AccessKey`, `s3SecretKey`) match what meeting-api actually passes to the container
