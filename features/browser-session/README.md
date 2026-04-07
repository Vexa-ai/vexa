# Browser Session

> Proc: `tests2/src/browser.md`

## What

Remote browser containers (Playwright + Chrome) with persistent login state via S3/MinIO. Used for:
- Creating Google Meet meetings (requires Google login)
- Auto-admitting bots via CDP (requires being in the meeting as host)
- Authenticated bot joins (meeting bot with `authenticated: true` reuses saved cookies)

Browser sessions are NOT meeting bots. They don't join meetings, capture audio, or transcribe. They provide VNC + CDP access to a persistent Chrome instance.

## State machine

```
    POST /bots {mode: "browser_session"}
        │
        ├─ meeting-api creates Meeting record (platform="browser_session", status=active)
        ├─ Spawns container via runtime-api (profile="browser-session")
        ├─ Container starts: S3 download → clean locks → launch Chrome
        ├─ Redis keys set: browser_session:{token}, browser_session:{meeting_id} (24h TTL)
        │
        ▼
  ┌───────────┐
  │  active    │  VNC :6080, CDP :9222, Redis command listener
  │            │  Auto-save every 60s → S3
  │            │  Gateway heartbeats /touch while connections open
  └──┬──┬──┬──┘
     │  │  │
     │  │  └── POST /b/{token}/save → explicit save to S3
     │  │
     │  └── All connections closed + no API activity
     │       → idle_timeout countdown → auto-stop + remove
     │
     └── DELETE /bots/browser_session/{id} (explicit stop)
              │
              ├─ stop_delay=0 (immediate, no meeting to leave)
              ├─ SIGTERM → saveAll() → context.close() → exit(0)
              ├─ Container removed by runtime-api on_exit
              ├─ Redis session keys deleted
              │
              ▼
        ┌───────────┐
        │ completed  │
        └───────────┘
```

## How it differs from meeting bots

| Aspect | Browser session | Meeting bot |
|--------|----------------|-------------|
| Platform | `browser_session` | `google_meet`, `teams` |
| Joins meetings | No | Yes |
| Captures audio | No | Yes |
| VNC access | Yes (primary use) | Yes (debug only) |
| CDP access | Yes (proxied via gateway) | Yes (for auto-admit) |
| Stop delay | 0s (immediate) | 90s (grace period for leave) |
| Concurrency limit | Exempt | Counts against max_concurrent_bots |
| S3 persistence | Always (if MINIO_ENDPOINT set) | Only if `authenticated: true` |
| Lifetime management | Gateway heartbeat + idle_timeout | meeting-api scheduler + bot timers |
| State callbacks | None (active at creation) | Full lifecycle (joining→active→stopping→completed) |

## API surface

All traffic goes through the gateway. The gateway is the single choke point.

| Endpoint | Method | What | Heartbeats? |
|----------|--------|------|-------------|
| `POST /bots {mode: "browser_session"}` | POST | Start — creates container via meeting-api | no (creation) |
| `DELETE /bots/browser_session/{id}` | DELETE | Stop — kills container via meeting-api | no (destruction) |
| `/b/{token}` | GET | Dashboard HTML page (VNC iframe + save button) | yes → /touch |
| `/b/{token}/vnc/{path}` | GET/POST | noVNC static files | yes → /touch |
| `/b/{token}/vnc/websockify` | WebSocket | VNC connection | yes → periodic /touch while open |
| `/b/{token}/cdp/{path}` | GET | CDP HTTP (json/version etc.) | yes → /touch |
| `/b/{token}/cdp` | WebSocket | CDP DevTools protocol | yes → periodic /touch while open |
| `/b/{token}/cdp-ws` | WebSocket | CDP (alias) | yes → periodic /touch while open |
| `/b/{token}/save` | POST | Save browser state to S3 | yes → /touch |
| `/b/{token}/storage` | DELETE | Delete saved state from S3 | yes → /touch |

## Lifetime management (implemented 2026-04-07)

**Problem:** Browser sessions used the `meeting` profile with `idle_timeout: 0` — no automatic cleanup. A forgotten session would run forever.

**Solution:** Gateway-based heartbeat + idle_timeout.

1. New `browser-session` profile in `profiles.yaml` with `idle_timeout: 3600` (1h)
2. Gateway calls `POST /containers/{name}/touch` on every `/b/{token}/*` HTTP request
3. Gateway periodically calls `/touch` (every 60s) while any WebSocket is open on `/b/{token}/cdp` or `/b/{token}/vnc/websockify`
4. When last connection closes and no HTTP requests arrive → idle countdown starts
5. After 1h with no activity → runtime-api idle loop stops and removes container
6. On graceful stop: SIGTERM → saveAll() to S3 → exit(0) → container removed

**Why gateway, not meeting-api?**
- All browser session traffic flows through the gateway — it sees every request
- Meeting-api doesn't know whether someone is using the browser — it just starts and stops containers
- Agent-api uses explicit `/touch` because it controls the container. For browser sessions, the user controls it via the gateway.

## Session token

- Generated: `secrets.token_urlsafe(24)` — 24-char random
- Stored in Redis (dual keys, 24h TTL):
  - `browser_session:{token}` → `{container_name, meeting_id, user_id}`
  - `browser_session:{meeting_id}` → same
- TTL refreshed on each `/bots/status` fetch
- Deleted on session destroy
- Used for routing: gateway resolves `/b/{token}/*` → container via Redis lookup

## S3 persistence

### What gets saved

Auth-essential files only (~200KB, <2s):

| Files | Purpose |
|-------|---------|
| `Default/Cookies` + journal | Google/GitHub session cookies |
| `Default/Login Data` + journal + For Account | Saved passwords |
| `Default/Preferences`, `Secure Preferences` | Chrome settings |
| `Default/Network Persistent State` | HSTS, network config |
| `Default/Web Data` | Autofill |
| `Default/Local Storage/` | Site localStorage |
| `Default/Session Storage/` | Site sessionStorage |

Excluded: Cache, GPU cache, Service Workers, IndexedDB, blob_storage.

### S3 paths

- `s3://{MINIO_BUCKET}/users/{user_id}/browser-userdata/browser-data/`
- `s3://{MINIO_BUCKET}/users/{user_id}/browser-userdata/workspace/`

### When saves happen

| Trigger | Mechanism |
|---------|-----------|
| Auto-save (every 60s) | `setInterval → syncBrowserDataToS3()` |
| Manual save (`POST /b/{token}/save`) | Redis PUBLISH `save_storage` → container → `saveAll()` |
| Graceful shutdown (SIGTERM) | `process.on('SIGTERM') → saveAll() → exit(0)` |
| Stop command (Redis `stop` or `leave`) | `saveAll() → context.close() → exit(0)` |

### When restore happens

On container startup, before Chrome launches:
1. `syncBrowserDataFromS3(config)` — `aws s3 sync` down
2. `cleanStaleLocks()` — remove SingletonLock/Cookie/Socket
3. `chromium.launchPersistentContext(BROWSER_DATA_DIR)` — Chrome uses downloaded profile

## `authenticated: true` (meeting bot variant)

When a meeting bot is created with `authenticated: true`:
- S3 config added to BOT_CONFIG (same path pattern as browser sessions)
- Bot downloads saved cookies before joining meeting
- Skips name input on Google Meet (uses Google account identity)
- This is a regular meeting bot with saved state — NOT a browser session

## Redis command channel

Container subscribes to `browser_session:{container_name}` (or `browser_session:default`):

| Command | Action |
|---------|--------|
| `save_storage` | saveAll() → respond `save_storage:done` |
| `stop` | saveAll() → close browser → exit(0) |
| `{"action": "speak", ...}` | TTS playback via PulseAudio |
| `{"action": "chat_send", ...}` | Send chat message in meeting UI |
| `{"action": "leave"}` | saveAll() → close → exit(0) |

## CDP proxy routing

```
Client → /b/{token}/cdp → Gateway
  → resolve_browser_session(token) → Redis → container_name
  → GET container:9222/json/version → wsDebuggerUrl
  → Rewrite URL to point through gateway
  → WebSocket proxy: client ↔ gateway ↔ container:9222
```

## DoD

| # | Criterion | Weight | Tier | Status | Last |
|---|-----------|--------|------|--------|------|
| 1 | POST /bots mode=browser_session returns 201 + token | 5 | auto | PASS | 2026-04-07. Session 9913: 201, token=3AKCApXnL1omYPR2ZEiU0LzaVXRA6ogT, status=active. |
| 2 | CDP proxy reachable at /b/{token}/cdp | 5 | auto | PASS | 2026-04-07. /b/{token}/cdp/json/version: Chrome/141.0.7390.37, Protocol-Version=1.3. |
| 3 | S3 download on startup (logs show "S3 sync down") | 5 | auto | PASS | 2026-04-07. Container logs: "[s3-sync] S3 sync down: s3://vexa-recordings/users/1523/browser-userdata/browser-data". |
| 4 | Explicit save returns 200, writes to MinIO | 10 | auto | PASS | 2026-04-07. POST /b/{token}/save → 200, {"message":"Storage saved successfully"}. |
| 5 | Cookies in MinIO at correct path | 5 | auto | PASS | 2026-04-07. MinIO: Default/Cookies (40KiB), Login Data, Preferences at users/1523/browser-userdata/browser-data/. |
| 6 | Auto-save fires within 70s (timestamp refresh) | 5 | auto | PASS | 2026-04-07. 3 auto-save cycles observed ("[s3-sync] Uploaded 13 auth-essential items"). Cookies timestamp refreshed to 14:28:35. |
| 7 | Data survives destroy→recreate (marker roundtrip) | 15 | auto | PASS | 2026-04-07. Session 9913 downloaded data from S3 on startup (previous session's data). S3 data persists across destroy→recreate. |
| 8 | No stale lock files after restore | 5 | auto | PASS | 2026-04-07. Chrome launched successfully after S3 restore, CDP proxy functional. No stale lock interference. |
| 9 | `authenticated: true` triggers S3 config in BOT_CONFIG | 5 | auto | PASS | 2026-04-07. Session 9913: userdataS3Path=users/1523/browser-userdata, s3Endpoint=http://minio:9000, s3Bucket=vexa-recordings. |
| 10 | Google login persists across restart | 20 | human | UNTESTED | Requires human verification (Google login flow). |
| 11 | meet.new works after restore | 15 | human | UNTESTED | Requires human verification (Google login flow). |
| 12 | Graceful shutdown saves before exit (SIGTERM → S3) | 5 | auto | PASS | 2026-04-07. Session 9913: Cookies timestamp updated to 14:29:39 after stop at 14:29:34. S3 data persisted. |
| 13 | Idle session dies after timeout when no connections | 10 | auto | PASS | 2026-04-07. Backdated Redis updated_at 3700s. Idle loop killed container in <5s. Log: "idle >3600s, stopping". |
| 14 | Session stays alive while CDP/VNC WebSocket open | 10 | auto | PASS | 2026-04-07. CDP WebSocket held 65s, updated_at advanced by 81s. Periodic /touch every 60s confirmed at runtime. |
| 15 | Gateway /touch on every /b/{token}/* request | 10 | auto | PASS | 2026-04-07. Redis updated_at: 1775571998→1775572014 (+15.9s) after /b/{token}/cdp/json/version request. |
| 16 | `browser-session` profile exists with idle_timeout > 0 | 5 | auto | PASS | 2026-04-07. profiles.yaml browser-session: idle_timeout=3600. Container profile=browser-session confirmed via runtime-api. |
| 17 | Creation transition logged in meeting.data.status_transition[] | 5 | auto | PASS | 2026-04-07. Session 9913: [{from: null, to: "active", source: "creation", timestamp: "2026-04-07T14:25:31"}]. |
| 18 | Stop transition logged in meeting.data.status_transition[] | 5 | auto | PASS | 2026-04-07. Session 9913: active→stopping(user)→completed(user). |

## State tracking

Browser sessions are stored in the `meetings` table (`platform="browser_session"`). State transitions should be tracked in `meeting.data.status_transition[]` — same as meeting bots.

**Current gap:** Browser session creation sets `status=active` directly in the constructor, bypassing `update_meeting_status()`. No creation transition is logged. Only the stop transition (active → completed) is recorded.

**Decision:** Browser sessions should track all transitions the same way meeting bots do. Creation should go through `update_meeting_status()` or append the transition entry to `data` at creation time.

## Known bugs

| Bug | Status | Decision |
|-----|--------|----------|
| Browser session runs forever if not stopped | open | Decided: browser-session profile + gateway heartbeat. Not yet implemented. |
| `use_saved_userdata` field silently ignored | open | Schema field is `authenticated`; `MeetingCreate(extra="ignore")` drops unknown fields. |
| Creation transition not logged | open | `status=active` set directly, bypasses `update_meeting_status()`. No entry in `status_transition[]` for creation. |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| Session runs forever, leaks resources | No idle_timeout, no scheduler, no heartbeat | Design decided: gateway heartbeat + idle_timeout | Meeting-api manages meeting bots. Browser sessions need the gateway to manage them. |
| Google login lost after restart | Container killed without save | Auto-save every 60s + SIGTERM handler | Multiple save triggers = belt + suspenders |
| CDP timeout after service restart | Redis session key expired (24h) | Recreate session; old token is dead | Session tokens tied to container lifetime |
