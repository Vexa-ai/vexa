---
id: test/create-live-meeting
type: validation
requires: [test/browser-session]
produces: [MEETING_URL, MEETING_PLATFORM]
validates: [meeting-urls]
docs: [features/meeting-urls/README.md]
mode: machine
skill: /test-create-meeting
---

# Create Live Meeting

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Open a new Google Meet meeting in a browser session that has saved login state. Extracts the meeting URL for bot testing.

## Inputs

| Name | From | Default | Description |
|------|------|---------|-------------|
| API_TOKEN | test/api-full | — | API token with browser scope |
| GATEWAY_URL | test/infra-up | — | API gateway URL |
| SAVED_STATE | test/browser-session | — | Confirms browser has saved Google login |

## Script

```bash
eval $(./scripts/06-create-meeting.sh CONTAINER [CDP_PORT])
```

See [06-create-meeting.sh](scripts/06-create-meeting.sh) for implementation.

## Prerequisite: authenticated session

Before attempting to create a meeting, check MinIO for saved browser userdata:

```bash
docker exec vexa-staging-minio-1 mc ls local/vexa-staging/users/ --recursive 2>/dev/null | grep -c "Cookies\|Login"
```

**If no saved session exists → STOP.** Notify the human to log in via the dashboard. This is the only human step in the entire pipeline.

**Once saved session exists, everything from here through Layer 4d is fully automated.** The script creates an authenticated browser session, navigates to meet.new, and extracts the meeting URL — no human interaction needed.

## Browser access pattern

All browser manipulation uses Playwright CDP via the gateway proxy:

```javascript
const { chromium } = require('playwright');
const browser = await chromium.connectOverCDP('$GATEWAY_URL/b/$SESSION_TOKEN/cdp');
const page = browser.contexts()[0].pages()[0];
```

The gateway proxies CDP WebSocket to the browser container. `SESSION_TOKEN` comes from the bot creation response (`data.session_token`). This runs from the **host** — no docker exec needed.

## Steps

1. Create authenticated browser session via `POST /bots` with `mode: "browser_session"` and `authenticated: true`
2. Connect to browser via `$GATEWAY_URL/b/$SESSION_TOKEN/cdp` using Playwright
3. Navigate to meet.new — waits for redirect to meet.google.com/xxx-xxxx-xxx
4. Extract and output MEETING_URL

> assert: URL matches `meet.google.com/xxx-xxxx-xxx` pattern
> on-fail: is Google login still valid? check if saved cookies expired. Human may need to re-login.

## Outputs

| Name | Description |
|------|-------------|
| MEETING_URL | Full Google Meet URL (e.g. `https://meet.google.com/abc-defg-hij`) |
| MEETING_PLATFORM | `google_meet` |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| meet.new redirects to login page | Saved cookies expired or not loaded | Human re-login via browser-session, save state again | |
| URL stays at meet.new (no redirect) | Google account doesn't have Meet enabled | Use a Google Workspace account, not consumer | |

## Docs ownership

After this test runs, verify and update:

- **features/meeting-urls/README.md**
  - DoD table: update Status, Evidence, Last checked for item #1 (Google Meet URL parsed correctly) — this test produces a real Google Meet URL and verifies it matches the `meet.google.com/xxx-xxxx-xxx` pattern
  - Supported formats table: verify the Google Meet format (`meet.google.com/{code}`, `meet.new` redirect) matches the actual URL format produced by `meet.new` navigation — if Google changed the URL structure, update the pattern
  - Components table: verify the bot creation path (`services/meeting-api/meeting_api/meetings.py`) correctly constructs a meeting from the extracted Google Meet URL parts
