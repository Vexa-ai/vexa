# tests3 — Fail-fast test system

Real code. No agent interpretation. Runs in CI, pre-commit, or manually.

## Quick start

```bash
make -C tests3 smoke     # 28 checks, ~15s, no meetings
make -C tests3 e2e       # smoke + live meeting chain, ~10min
make -C tests3 locks     # just static regression locks, <1s
make -C tests3 clean     # clear state from previous runs
make -C tests3 help      # list all targets
```

## Architecture

Two kinds of targets, one Makefile.

**Checks** are registry-driven. Each check is one entry in `checks/registry.json` with a tier. The runner filters by tier and executes. Adding a check = adding a JSON entry.

**Tests** are script-driven. Each script is an orchestrated sequence (create meeting, launch bot, send TTS, score). They read/write state via `.state/` files.

```
tests3/
├── Makefile              # all targets
├── checks/
│   ├── registry.json     # every check, classified by tier
│   └── run               # unified runner (python3)
├── tests/
│   ├── meeting.sh        # create GMeet via CDP
│   ├── bot.sh            # launch + poll recorder
│   ├── transcribe.sh     # send TTS, fetch transcript, score
│   └── finalize.sh       # stop bots, verify cleanup
├── lib/
│   ├── common.sh         # svc_exec, state helpers, http helpers
│   └── detect.sh         # auto-detect compose/lite/helm
└── .state/               # runtime state (gitignored)
```

## Targets

### Checks (registry-driven, instant)

| Target | Tier | What it checks | Needs |
|--------|------|---------------|-------|
| `locks` | static | Grep source code for known-fixed bugs | Source code |
| `env` | env | Env var consistency across containers | Running containers |
| `health` | health | Service endpoints respond | Network |
| `contracts` | contract | API behavior (POST /bots, WS ping, login) | Network + credentials |
| `smoke` | all | All of the above in order | Running deployment |

### Tests (script-driven, orchestrated)

| Target | What it does | Needs | Time |
|--------|-------------|-------|------|
| `meeting` | Create Google Meet via CDP browser session | Browser with Google login | 60s |
| `bot` | Launch recorder bot, poll until active | Live meeting + human admit | 5min |
| `transcribe` | Send TTS utterances, fetch transcript, score | Active bot in meeting | 2min |
| `e2e` | smoke → meeting → bot → transcribe → finalize | Everything | 10min |

### Util

| Target | What it does |
|--------|-------------|
| `clean` | Clear `.state/` from previous runs |
| `help` | List all targets |

## Fail-fast tiers

Each tier gates the next. A failure stops everything — no point testing API contracts if services are down.

```
static  →  env  →  health  →  contract  →  meeting  →  bot  →  transcribe
  0s        2s       5s         15s          60s        5min      2min
 grep    docker    curl      curl+auth      CDP       poll      TTS+score
         exec
```

## Cross-deployment support

Same checks, any deployment. The only abstraction: `svc_exec` and URL variables.

```bash
# Local compose (auto-detected)
make -C tests3 smoke

# Lite on a VM
make -C tests3 smoke DEPLOY_MODE=lite

# Helm on staging
make -C tests3 smoke DEPLOY_MODE=helm \
  GATEWAY_URL=https://api.staging.vexa.ai \
  DASHBOARD_URL=https://app.staging.vexa.ai
```

`svc_exec` routes container commands:

| Mode | `svc_exec dashboard printenv X` becomes |
|------|----------------------------------------|
| compose | `docker exec vexa-dashboard-1 printenv X` |
| lite | `docker exec vexa printenv X` |
| helm | `kubectl exec deploy/dashboard -- printenv X` |

## Registry format

Every check is a JSON entry in `checks/registry.json`:

```json
{
  "id": "LOGIN_REDIRECT",
  "tier": "static",
  "found": "2026-04-07",
  "symptom": "After login, user redirected to /agent instead of /meetings",
  "file": "services/dashboard/src/app/login/page.tsx",
  "must_match": "push(\"/\")",
  "must_not_match": "push\\(\"/agent\"\\)"
}
```

### Tier-specific fields

**static** — grep source files:
- `file`: path relative to repo root
- `must_match`: literal substring that must be present
- `must_not_match`: regex pattern that must NOT be present

**env** — compare env vars across containers:
- `env_checks[].service`: container to exec into
- `env_checks[].var`: env var name
- `env_checks[].not_empty`: must have a value
- `env_checks[].equals`: `{service, var}` — must match another container's var
- `env_checks[].valid_against`: `{url, header}` — use the value as a credential

**health** — curl endpoints:
- `url`: endpoint (supports `$GATEWAY_URL`, `$ADMIN_URL`, `$DASHBOARD_URL`)
- `expect_code`: HTTP status (int or list)
- `needs_admin_token`: bootstrap token from container env

**contract** — test API behavior:
- `url`, `method`, `data`, `auth` (`api_token` or `admin_token`), `expect_code`
- `method: WS_PING` for WebSocket checks

## Adding a check

When you fix a bug:

1. Fix the code
2. Add one entry to `registry.json` with the right tier
3. Run `make -C tests3 locks` to verify it passes

That's it. The check runs automatically on every `make smoke`.

## Current coverage

39 registry checks + 13 test scripts, covering ~120 of 153 DoD criteria (78%).

### Registry checks (39)

| Tier | Count | What |
|------|-------|------|
| static | 12 | Regression locks: redirect, identity, cookies, routes, graceful leave, URL parser, mapMeeting, compose defaults, password-store |
| env | 7 | Dashboard keys match admin-api, keys valid against API, VEXA_API_URL set, MINIO_ENDPOINT, MINIO_BUCKET, RUNTIME_API_URL |
| health | 7 | Gateway, admin-api, dashboard, runtime-api, transcription, redis, minio |
| contract | 13 | /bots/status, /meetings, auth rejection, 5 Teams URL formats, GMeet URL, invalid URL, WS ping, dashboard login, cache headers |

### Test scripts (13)

| Script | Feature | DoDs covered |
|--------|---------|-------------|
| `dashboard-auth.sh` | Dashboard | Login, cookie flags, /me identity, proxy reachable |
| `dashboard-proxy.sh` | Dashboard | Meetings list, pagination, field contract, transcript proxy, bot via proxy, false-failed |
| `containers.sh` | Container + Bot lifecycle | Create/stop/remove, timeout auto-stop, concurrency release, orphan check |
| `browser-session.sh` | Browser session | Create, CDP, S3 save/restore roundtrip, auth flag, cleanup |
| `browser-login.sh` | Browser session | [human] Google login persistence, meet.new works |
| `meeting.sh` | Meeting | Create GMeet via CDP |
| `bot.sh` | Bot lifecycle | Launch recorder, poll status transitions |
| `admit.sh` | Bot lifecycle | Multi-phase CDP auto-admit (GMeet + Teams) |
| `transcribe.sh` | Transcription | TTS utterances, transcript fetch, basic quality score |
| `finalize.sh` | Bot lifecycle | Stop bots, verify completed, orphan check |
| `post-meeting.sh` | Post-meeting | Recordings, deferred transcription, dedup, speaker attribution |
| `webhooks.sh` | Webhooks | Envelope shape, HMAC, no secret leak, no internal fields |
| `auth-meeting.sh` | Authenticated meetings | S3 config, cookie download, Chrome context, screenshot, shared path, use_saved_userdata |

### Remaining gaps (~33 DoDs)

| Gap | Why | What would cover it |
|-----|-----|-------------------|
| Dashboard render in headless browser | Needs Playwright installed | `tests/dashboard-render.sh` |
| Browser session idle timeout | 3600s timeout impractical to test | Verify mechanism, not wall-clock |
| Gateway /touch on WS connections | Needs long-lived WS + timer check | `tests/browser-ws-touch.sh` |
| Token scope enforcement | Complex multi-token setup | Registry contract entries |
| Rate limiting (429) | Not implemented | — |
| Zoom | Not implemented | — |
| Speaker accuracy (WER < 15%) | Needs human speech, not just TTS | tests2 rt-replay proc |
| K8s profile propagation | Needs helm deployment | Registry entries with DEPLOY_MODE=helm |

## Relationship to tests2

tests2 is agent-interpreted markdown procs. tests3 is executable code.

- tests2 procs are useful for **discovery** — debugging, finding new bugs, evaluating transcription quality
- tests3 checks are useful for **enforcement** — preventing known bugs from returning, validating deployments

The workflow: agent runs tests2 procs, finds a bug, fixes code, adds a tests3 registry entry. The bug never comes back.

| | tests2 | tests3 |
|--|--------|--------|
| Format | Markdown procs | Shell/Python scripts |
| Runs via | Agent interprets | `make` targets |
| Speed | Minutes-hours | Seconds-minutes |
| Purpose | Discovery, deep validation | Regression prevention |
| When | Before release, debug sessions | Every code change, CI |
