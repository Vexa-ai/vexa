---
id: test/dashboard-validation
type: validation
requires: [test/infra-up]
produces: [DASHBOARD_URL]
validates: [remote-browser, auth-and-limits]
docs: [features/remote-browser/README.md, features/auth-and-limits/README.md, services/dashboard/README.md]
mode: machine
skill: /validate-dashboard
---

# Dashboard Validation

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Validates the dashboard works before telling a human to use it. Runs every backend call **from inside the dashboard container** — same URLs, same headers, same perspective.

## Why

Next.js returns 200 even when all backends are broken. The human gets `[object Object]`. This catches it first.

## Backend call map

Extracted from dashboard compiled JS (`/app/.next/server/chunks/*.js`):

### GET calls (connectivity)

| Variable | Calls (method + path) | Used for |
|----------|----------------------|----------|
| `$VEXA_API_URL` | `GET /` | gateway alive |
| `$VEXA_API_URL` | `GET /meetings` (+ X-API-Key) | meetings page |
| `$VEXA_API_URL` | `GET /bots/status` (+ X-API-Key) | bot status |
| `$VEXA_ADMIN_API_URL` | `GET /admin/users?limit=1` (+ X-Admin-API-Key) | login flow |
| `$VEXA_ADMIN_API_URL` | `GET /admin/users/email/:email` (+ X-Admin-API-Key) | login by email |
| `$VEXA_ADMIN_API_URL` | `GET /admin/users/:id` (+ X-Admin-API-Key) | user detail |
| `$VEXA_PUBLIC_API_URL` | `GET /` | client-side JS (SKIP_INSIDE — client-side only) |
| `localhost:3000` | `GET /api/auth/session` | internal Next.js auth |

### POST calls (user actions — these cause `[object Object]` when broken)

| Via | Calls (method + path) | Used for |
|-----|----------------------|----------|
| `/api/vexa/bots` → `$VEXA_API_URL/bots` | `POST {mode: "browser_session"}` | browser session creation |
| `/api/vexa/bots` → `$VEXA_API_URL/bots` | `POST {platform, native_meeting_id, bot_name}` | meeting bot join |

### Webhook backend (dashboard /webhooks page)

All webhook operations go through the dashboard's Next.js API routes, which proxy to the admin API.
Auth: `vexa-token` + `vexa-user-info` cookies (both required by `getAuthenticatedUserId()`).

| Via | Method + path | Used for |
|-----|--------------|----------|
| `/api/webhooks/config` | `GET` | load webhook URL, secret, event toggles |
| `/api/webhooks/config` | `PUT {endpoint_url, events}` | save webhook configuration |
| `/api/webhooks/test` | `POST {url}` | send test webhook to configured endpoint |
| `/api/webhooks/rotate-secret` | `POST {}` | generate new signing secret |
| `/api/webhooks/deliveries` | `GET ?time_range=7d&status=` | delivery history (real + test) |

### API Keys backend (dashboard /profile page)

All API key operations go through the dashboard's Next.js API routes, which proxy to the admin API.
Auth: `vexa-token` + `vexa-user-info` cookies (both required by `getAuthenticatedUserId()`).

| Via | Method + path | Used for |
|-----|--------------|----------|
| `/api/profile/keys` | `GET` | list user's API keys |
| `/api/profile/keys` | `POST {name, scopes, expires_in}` | create new API key |
| `/api/profile/keys/:id` | `DELETE` | revoke API key |

**API key display issue:** The profile page shows "No API keys yet" when `getAuthenticatedUserId()` fails.
This function requires BOTH cookies: `vexa-token` (the API key) AND `vexa-user-info` (JSON with `email` field).
If the user logged in via direct token (not magic link), `vexa-user-info` may be missing, causing 401 from
the keys endpoint, which the frontend treats as "no keys". The test validates both cookies are working.

### Transcript visibility (this test owns it)

After creating a meeting bot that produces transcription, the dashboard must
show segments on the meeting page. This validates the full chain:
bot → transcription-service → Redis → Postgres → REST → dashboard render.

| Check | How | Expected |
|-------|-----|----------|
| VEXA_API_KEY has `tx` scope | `docker exec dashboard printenv VEXA_API_KEY` → check scopes in DB | Token must include `tx` scope, not just `bot` |
| Transcript page returns segments | `GET /transcripts/{platform}/{id}` with dashboard token | ≥1 segment after bot runs |
| Browser session page loads without 403 | Create browser_session → navigate to meeting page | No "Insufficient scope" error, VNC renders |
| Active meeting shows live segments | View meeting page during active transcription | Segments appear via WS or polling |

**Root cause of blank transcripts:** The `VEXA_API_KEY` created by `make setup-api-key`
defaults to `bot` scope only. Transcript endpoints require `tx` scope. Fix: create the
dashboard key with `scopes=bot,browser,tx`.

**Root cause of browser session 403:** Dashboard's transcript-fetch `useEffect` fires
for browser_session meetings even though they don't show transcripts. When the user's
cookie is missing, the fallback `VEXA_API_KEY` (bot-only) hits the transcript endpoint
and gets 403. Fix applied: skip transcript fetch for active browser sessions, and render
BrowserSessionView before the error guard.

The dashboard proxy reads `vexa-token` cookie and forwards as `X-API-Key`. The 422 `[object Object]` error happens when the gateway rejects the POST body. The gateway requires `platform` + `native_meeting_id` (not `meeting_url`). The dashboard client-side parses URLs to extract these, but also passes `meeting_url` as an extra field.

## Script

```bash
eval $(./testing/04-dashboard.sh)
```

The script iterates the call map above from inside the dashboard container. Each call is exactly as the dashboard makes it. PASS = all respond. FAIL = specific call identified.

## Outputs

| Name | Description |
|------|-------------|
| DASHBOARD_URL | `http://localhost:$DASHBOARD_PORT` (only if all checks pass) |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| `[object Object]` on browser page | Agent API unreachable at hardcoded localhost:8100 | Add VEXA_AGENT_API_URL to compose or fix dashboard code | Dashboard hardcodes agent-api — broken in Docker network |
| Dashboard 200 but pages error | Backend down or env vars wrong | Run script, find which call fails | HTTP 200 from Next.js means nothing |
| VEXA_PUBLIC_API_URL unreachable | Container can't reach host network | Add extra_hosts or use internal URL | Client-side calls go via host |
| `/admin/users?limit=1` 401 | VEXA_ADMIN_API_KEY wrong | Verify token matches admin-api ADMIN_API_TOKEN | Login flow depends on this |
| All calls → 000 from inside container | curl not installed in Next.js image | Use wget instead of curl | Next.js Docker images ship with wget, not curl |
| public API URL → 000 from inside | VEXA_PUBLIC_API_URL=localhost:8066 is host-only | Test from host, not inside container (SKIP_INSIDE) | Client-side URL — container can't reach host's localhost |
| public API URL → 405 | wget --spider sends HEAD, gateway rejects | Use wget -S -O /dev/null (GET) instead of --spider | Some APIs don't support HEAD |
| POST /bots → 422 `[object Object]` | Gateway rejects `meeting_url` field — requires `platform` + `native_meeting_id` | Gateway must accept `meeting_url` OR dashboard must always parse to platform+native_meeting_id | GET-only validation hid this — always test POST paths too |
| Tests pass but dashboard broken | Validation only tested GET endpoints, missed POST /bots | Added POST tests to 04-dashboard.sh | GET connectivity ≠ user actions working |
| POST test passes but dashboard 422 | Test sent minimal payload, dashboard sends extra fields (workspaceGitRepo/Token/Branch from localStorage) | Test must send exact dashboard payload including all fields | Never sanitize test payloads — send what the real UI sends |
| 422 `extra_forbidden` on browser create | MeetingCreate schema has `extra="forbid"`, dashboard sends workspace git fields | Add workspace fields to MeetingCreate schema | Dashboard and API schema must agree on accepted fields |
| 403 on browser_session page load, gone on retry | First SSR fetch to `/api/vexa/transcripts/browser_session/{id}` doesn't include auth cookie — race between page render and cookie setup | Fix SSR fetch to pass cookie, or make client-side with proper auth | Intermittent 403 on first load wastes human time and looks broken. Test must create a browser_session then immediately fetch its transcript page |
| Webhook config → 401 | `vexa-user-info` cookie missing — `getAuthenticatedUserId()` requires both `vexa-token` AND `vexa-user-info` | Set both cookies in test; in dashboard, ensure login flow sets both | All server-side API routes that use `getAuthenticatedUserId()` need both cookies |
| API keys shows "No keys" | Same auth issue — `getAuthenticatedUserId()` returns null → 401 → frontend shows empty list | Ensure login flow sets `vexa-user-info` cookie with `{email}` JSON | Profile page silently swallows auth errors and shows "No API keys yet" instead of an error message |
| Webhook test → timeout | httpbin.org unreachable from container | Test from host or use internal test endpoint | Container network may not have external access |

## Docs ownership

After this test runs, verify and update:

- **features/remote-browser/README.md**
  - DoD table: update Status, Evidence, Last checked for item #4 (VNC accessible via dashboard) — this test validates the dashboard can render the VNC iframe for browser sessions
  - Components table: verify `services/vexa-bot/core/src/browser-session.ts` and `services/api-gateway/main.py` CDP proxy paths are correct

- **features/auth-and-limits/README.md**
  - DoD table: update Status, Evidence, Last checked for item #6 (Dashboard token VEXA_API_KEY has correct scopes) — this test checks that the dashboard's API key includes `bot,browser,tx` scopes
  - Components table: verify the token validation path in `services/api-gateway/main.py` matches how the dashboard proxy forwards `X-API-Key`

- **services/dashboard/README.md**
  - Required Configuration table: verify `VEXA_API_URL`, `VEXA_ADMIN_API_KEY`, and optional `VEXA_ADMIN_API_URL` env var names match what the running dashboard container actually has (the test reads these from inside the container)
  - URL Proxy Pattern table: verify `/api/vexa/*` proxies to `${VEXA_API_URL}/*` and `/b/*` proxies to `${VEXA_API_URL}/b/*` — the test exercises both GET and POST through these proxy paths
  - Troubleshooting section: verify "Login or admin routes fail" and "Dashboard loads but data is empty" advice matches actual failure modes observed — if the backend call map reveals new failure patterns, add them
  - Recording Playback section: if the test created a browser_session and checked the meeting detail page, verify the documented `GET /recordings/{id}/media/{mid}/raw` endpoint behavior matches what the dashboard actually calls
