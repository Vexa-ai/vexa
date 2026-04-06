# Remote Browser

## Why

Users need authenticated browser sessions for meeting hosting (GMeet auto-host), and developers need remote browser access for debugging. The browser runs in a Docker container with VNC + CDP access through the API gateway.

## What

```
POST /bots {mode: "browser_session"} → container with Playwright + Xvfb + VNC + CDP
  → user accesses via dashboard VNC iframe or CDP proxy
  → browser state (cookies, login) saved to MinIO, restored on next session
```

### Components

| Component | File | Role |
|-----------|------|------|
| browser-session | `services/vexa-bot/core/src/browser-session.ts` | Persistent context, Redis commands, state sync |
| s3-sync | `services/vexa-bot/core/src/s3-sync.ts` | Auth-essential file upload/download to MinIO |
| entrypoint | `services/vexa-bot/core/entrypoint.sh` | Xvfb, PulseAudio, VNC, websockify, SSH |
| CDP proxy | `services/api-gateway/main.py` | Gateway proxies CDP WebSocket to container |

## How

### 1. Create a browser session

```bash
curl -s -X POST http://localhost:8056/bots \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"mode": "browser_session"}'
# {"bot_id": 59, "status": "requested", "mode": "browser_session", ...}
```

### 2. Access the browser via CDP proxy

The gateway proxies Chrome DevTools Protocol (CDP) WebSocket connections to the container:

```bash
# Check CDP is reachable through the gateway
curl -s http://localhost:8056/cdp/59/json/version
# {"Browser": "Chrome/...", "webSocketDebuggerUrl": "ws://..."}

# Connect via CDP WebSocket (e.g., from Playwright or puppeteer)
# ws://localhost:8056/cdp/59/devtools/browser/<guid>
```

### 3. Access VNC via the dashboard

Open the dashboard at `http://localhost:3001`, navigate to the browser session, and interact with the remote browser via the embedded VNC iframe.

### 4. Persist login state

Log into a Google account in the browser session. The session cookies and auth state are saved to MinIO automatically. On the next `POST /bots {"mode": "browser_session"}`, the state is restored -- the account stays logged in.

### 5. Stop the browser session

```bash
curl -s -X DELETE -H "X-API-Key: $VEXA_API_KEY" \
  http://localhost:8056/bots/browser/59
# 200 {"status": "stopping"}
# Transitions: stopping -> completed (container removed)
```

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | Browser session creates and container runs | 20 | ceiling | 0 | PASS | Session created via API (id=59) | 2026-04-05T19:40Z | 05-browser-session |
| 2 | CDP accessible through gateway proxy | 20 | ceiling | 0 | PASS | CDP accessible through gateway proxy | 2026-04-05T19:40Z | 05-browser-session |
| 3 | Login state persists across sessions (save + load) | 25 | ceiling | 0 | PASS | Google login persists across sessions | 2026-04-05T19:40Z | 05-browser-session |
| 4 | VNC accessible via dashboard | 15 | — | 0 | PASS | Dashboard POST browser_session → 201, CDP proxy verified, VNC path operational | 2026-04-05T19:40Z | 04-dashboard, 05-browser-session |
| 5 | Container stops cleanly on DELETE /bots | 10 | — | 0 | PASS | Browser session 131 (bs-c9559233): completed, reason=stopped, chain=stopping→completed | 2026-04-05T21:40Z | 11-finalization |
| 6 | No orphan containers after session ends | 10 | — | 0 | PARTIAL | No orphan Chrome processes. But 7 zombie node processes from meeting bots exist in container (BUG #20). Browser session itself cleaned up. | 2026-04-05T21:41Z | 14-container-lifecycle |

Confidence: 90 (ceiling items 1+2+3 = 65, items 4+5 = 25; item 6 partial — browser session cleaned up but zombie node processes exist from other bots)

## Known Issues

### CDP proxy hardcodes port 9223, Chrome on 9222 in lite mode (bug #21)

The API gateway's CDP proxy (`services/api-gateway/main.py`) hardcodes the target port as 9223. In compose mode, the bot entrypoint runs `socat` to expose Chrome's internal port 9222 on 0.0.0.0:9223 for Docker network access. In lite mode (process backend), there is no socat — Chrome listens on 9222 directly. The gateway's hardcoded 9223 fails to connect.

**Impact:** CDP proxy broken in lite mode. Compose mode unaffected.

**Workaround:** In lite mode, the gateway container is the same as the bot container, so the CDP connection can use localhost:9222 directly. Fix: make the CDP target port configurable or detect the deployment mode.
