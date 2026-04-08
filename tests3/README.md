# tests3 — Fail-fast test system

## Why

Vexa has multiple services, three deployment modes, and an end-to-end chain from browser automation to transcription scoring. A misconfigured env var silently breaks login. A regression in a redirect ships unnoticed. A healthy service doesn't mean the API contract is intact.

Every known bug should become a permanent lock. Every deployment should prove itself healthy. Failures should stop the chain immediately -- not after ten minutes of cascading timeouts.

## What

Two complementary strategies in one Makefile.

**Checks** are registry-driven. Each check is a JSON entry in `checks/registry.json` — a documented regression lock with a tier, a symptom, and a concrete assertion. The runner filters by tier and executes. Adding a check means adding one JSON object. No test files, no boilerplate.

**Tests** are script-driven. Each script orchestrates a real workflow: create a meeting via CDP, launch a bot, send TTS audio, fetch and score the transcript. Scripts read and write state via `.state/` files, which decouples stages so any step can be rerun independently or paused for human intervention.

Both strategies share a **tiered fail-fast pipeline**:

```
static  -->  env  -->  health  -->  contract  -->  meeting  -->  bot  -->  transcribe
  0s          2s        5s           15s            60s          5min        2min
 grep      docker     curl       curl+auth        CDP          poll      TTS+score
           exec
```

Each tier gates the next. If services are down, there's no point testing API contracts. If API contracts are broken, there's no point launching a bot. Failures isolate the root cause to a specific layer.

**Cross-deployment abstraction** makes the same checks work on compose, lite (single container), and helm. The only moving part: `svc_exec` routes container commands to `docker exec`, single-container exec, or `kubectl exec` depending on deployment mode.

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

## Workflow

Work locally. Deploy-test last.

```
1. Code change
   └─→ make -C tests3 locks          <1s    catch regressions before anything else

2. After restart / deploy
   └─→ make -C tests3 smoke          30s    env + health + contracts

3. Feature validation
   └─→ make -C tests3 dashboard      30s    login, proxy, pagination
   └─→ make -C tests3 containers     2min   bot lifecycle
   └─→ make -C tests3 webhooks       15s    envelope, HMAC
   └─→ make -C tests3 meeting-tts    10min  live meeting + TTS + transcript (human admits)

4. Before release (final gate)
   └─→ make -C tests3 vm-compose     6min   fresh VM, pull :dev, full smoke
   └─→ make -C tests3 vm-lite        5min   fresh VM, pull lite:dev, smoke
   └─→ make -C tests3 vm-destroy            tear down
```

Steps 1-3 are local, fast, iterative. Step 4 runs once when everything else is green. Don't iterate on VMs — debug locally, verify remotely.

## How

### Quick start

```bash
make -C tests3 smoke         # 40 checks, ~30s, no meetings
make -C tests3 meeting-tts   # smoke + live meeting + TTS + transcript, ~10min
make -C tests3 locks         # just static regression locks, <1s
make -C tests3 clean         # clear state from previous runs
make -C tests3 help          # list all targets
```

### Adding a regression lock

When you fix a bug, lock it:

1. Fix the code
2. Add one entry to `checks/registry.json` with the right tier
3. Run `make -C tests3 locks` to verify it passes

That's it. The check runs automatically on every `make smoke`.

### Targets

#### Checks (registry-driven, instant)

| Target      | Tier     | What it checks                            | Needs                 |
| ----------- | -------- | ----------------------------------------- | --------------------- |
| `locks`     | static   | Grep source code for known-fixed bugs     | Source code           |
| `env`       | env      | Env var consistency across containers     | Running containers    |
| `health`    | health   | Service endpoints respond                 | Network               |
| `contracts` | contract | API behavior (POST /bots, WS ping, login) | Network + credentials |
| `smoke`     | all      | All of the above in order                 | Running deployment    |

#### Tests (script-driven, orchestrated)

| Target       | What it does                                  | Needs                      | Time  |
| ------------ | --------------------------------------------- | -------------------------- | ----- |
| `meeting`    | Create Google Meet via CDP browser session    | Browser with Google login  | 60s   |
| `bot`        | Launch recorder bot, poll until active        | Live meeting + human admit | 5min  |
| `transcribe` | Send TTS utterances, fetch transcript, score  | Active bot in meeting      | 2min  |
| `e2e`        | smoke -> meeting -> bot -> transcribe -> finalize | Everything             | 10min |

#### Dashboard, containers, advanced

| Target         | What it does                                            | Time  |
| -------------- | ------------------------------------------------------- | ----- |
| `dashboard`    | Login, cookies, identity, proxy, meetings, transcripts  | 30s   |
| `containers`   | Create/stop/remove, timeout, concurrency, orphan check  | 2min  |
| `webhooks`     | Envelope shape, HMAC, secret safety                     | 15s   |
| `auth-meeting` | S3 config, cookies, Chrome context, screenshot          | 60s   |
| `post-meeting` | Recordings, deferred transcription, speaker attribution | 2min  |
| `full`         | Everything                                              | 15min |

#### VM provisioning

| Target       | What it does                               |
| ------------ | ------------------------------------------ |
| `vm-compose` | Provision Linode VM, deploy compose, smoke  |
| `vm-lite`    | Provision Linode VM, deploy lite, smoke     |
| `vm-destroy` | Tear down VM                               |
| `vm-ssh`     | SSH into provisioned VM                    |

#### Helm (staging cluster)

| Target            | What it does                                    |
| ----------------- | ----------------------------------------------- |
| `helm-check`      | Verify kubectl can reach the cluster            |
| `helm-smoke`      | helm-check + smoke (includes K8s-only checks)   |
| `helm-dashboard`  | helm-smoke + dashboard tests                    |
| `helm-containers` | helm-smoke + container lifecycle tests          |
| `helm-full`       | helm-check + full suite                         |

#### Utility

| Target  | What it does                       |
| ------- | ---------------------------------- |
| `clean` | Clear `.state/` from previous runs |
| `help`  | List all targets                   |

### Deployment testing

Each deployment mode (compose, lite, helm) gets validated against the same test suite on fresh infrastructure.

#### How it works

1. **Provision** -- `make vm-compose` or `make vm-lite` spins up a Linode VM (Ubuntu 24.04, g6-standard-6)
2. **Deploy** -- setup script installs Docker, clones the repo, deploys the target mode:
   - **compose**: `make all` in `deploy/compose/` -- multi-container stack (gateway, admin-api, dashboard, runtime, transcription, Redis, MinIO, Postgres)
   - **lite**: single `vexa` container + external Postgres -- `Dockerfile.lite` bundles all services into one image via supervisord
3. **Smoke** -- runs the same `make smoke` checks against the deployed target, plus mode-specific integration tests
4. **Tear down** -- `make vm-destroy` deletes the VM and clears state

```
local machine                          Linode VM
─────────────                          ─────────
make vm-compose ──provision──→  fresh Ubuntu 24.04
                ──scp tests3──→  /root/vexa/tests3/
                ──ssh setup──→   docker compose up
                ──ssh smoke──→   make -C tests3 smoke DEPLOY_MODE=compose
                               ← stream results back
make vm-destroy ──delete──→     gone
```

State files (`.state/vm_ip`, `.state/vm_id`, `.state/vm_mode`) track the active VM so `vm-ssh`, `vm-destroy`, and test runs know where to connect.

#### What each mode validates

| Mode    | Setup time | Services                                      | Tests run on VM                                        |
| ------- | ---------- | --------------------------------------------- | ------------------------------------------------------ |
| compose | ~6 min     | Multi-container (one per service)              | smoke, dashboard-auth, dashboard-proxy, containers, webhooks |
| lite    | ~5 min     | Single container + external Postgres           | smoke, dashboard-auth, containers                      |
| helm    | 0 (cluster exists) | K8s cluster (staging)            | smoke + K8s checks, dashboard, containers, full        |

#### Why fresh VMs

Local Docker caches images, configs, and volumes. A test that passes locally might fail on a clean deploy because of a missing env var default or an image layer that wasn't rebuilt. Fresh VMs catch what local runs miss.

### Cross-deployment usage

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

| Mode    | `svc_exec dashboard printenv X` becomes       |
| ------- | --------------------------------------------- |
| compose | `docker exec vexa-dashboard-1 printenv X`                   |
| lite    | `docker exec vexa printenv X`                               |
| helm    | `kubectl exec deploy/{release}-vexa-dashboard -- printenv X` |

### Registry format

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

#### Tier-specific fields

**static** -- grep source files:

- `file`: path relative to repo root
- `must_match`: literal substring that must be present
- `must_not_match`: regex pattern that must NOT be present

**env** -- compare env vars across containers:

- `env_checks[].service`: container to exec into
- `env_checks[].var`: env var name
- `env_checks[].not_empty`: must have a value
- `env_checks[].equals`: `{service, var}` -- must match another container's var
- `env_checks[].valid_against`: `{url, header}` -- use the value as a credential

**health** -- curl endpoints:

- `url`: endpoint (supports `$GATEWAY_URL`, `$ADMIN_URL`, `$DASHBOARD_URL`)
- `expect_code`: HTTP status (int or list)
- `needs_admin_token`: bootstrap token from container env

**contract** -- test API behavior:

- `url`, `method`, `data`, `auth` (`api_token` or `admin_token`), `expect_code`
- `method: WS_PING` for WebSocket checks

## Coverage

53 registry checks + 16 test scripts. Validated across compose, lite, and helm (LKE). Helm support: K8s health checks, bot lifecycle, browser session CDP, segment pipeline, recording config.

### Registry checks (53)

| Tier     | Count | What                                                                                                                            |
| -------- | ----- | ------------------------------------------------------------------------------------------------------------------------------- |
| static   | 14    | Regression locks (12) + helm chart lint + helm template render                                                                  |
| env      | 7     | Dashboard keys match admin-api, keys valid against API, VEXA_API_URL set, MINIO_ENDPOINT, MINIO_BUCKET, RUNTIME_API_URL         |
| health   | 11    | Gateway, admin-api, dashboard, runtime-api, transcription, redis, minio + K8s: deployments ready, no crashloop, no restarts, secrets exist |
| contract | 21    | Browser session CDP, bot recording, MinIO writable, segment pipeline, bot status transitions, bot create, /bots/status, /meetings, auth, URL formats, WS ping, dashboard login, transcription token, cache headers |

### Test scripts (13)

| Script               | Feature                   | DoDs covered                                                                             |
| -------------------- | ------------------------- | ---------------------------------------------------------------------------------------- |
| `dashboard-auth.sh`  | Dashboard                 | Login, cookie flags, /me identity, proxy reachable                                       |
| `dashboard-proxy.sh` | Dashboard                 | Meetings list, pagination, field contract, transcript proxy, bot via proxy, false-failed |
| `containers.sh`      | Container + Bot lifecycle | Create/stop/remove, timeout auto-stop, concurrency release, orphan check                 |
| `browser-session.sh` | Browser session           | Create, CDP, S3 save/restore roundtrip, auth flag, cleanup                               |
| `browser-login.sh`   | Browser session           | [human] Google login persistence, meet.new works                                         |
| `meeting.sh`         | Meeting                   | Create GMeet via CDP                                                                     |
| `bot.sh`             | Bot lifecycle             | Launch recorder, poll status transitions                                                 |
| `admit.sh`           | Bot lifecycle             | Multi-phase CDP auto-admit (GMeet + Teams)                                               |
| `transcribe.sh`      | Transcription             | TTS utterances, transcript fetch, basic quality score                                    |
| `finalize.sh`        | Bot lifecycle             | Stop bots, verify completed, orphan check                                                |
| `post-meeting.sh`    | Post-meeting              | Recordings, deferred transcription, dedup, speaker attribution                           |
| `webhooks.sh`        | Webhooks                  | Envelope shape, HMAC, no secret leak, no internal fields                                 |
| `auth-meeting.sh`    | Authenticated meetings    | S3 config, cookie download, Chrome context, screenshot, shared path, use_saved_userdata  |

### Remaining gaps (~33 DoDs)

| Gap                                  | Why                               | What would cover it                    |
| ------------------------------------ | --------------------------------- | -------------------------------------- |
| Dashboard render in headless browser | Needs Playwright installed        | `tests/dashboard-render.sh`            |
| Browser session idle timeout         | 3600s timeout impractical to test | Verify mechanism, not wall-clock       |
| Gateway /touch on WS connections     | Needs long-lived WS + timer check | `tests/browser-ws-touch.sh`            |
| Token scope enforcement              | Complex multi-token setup         | Registry contract entries              |
| Rate limiting (429)                  | Not implemented                   | --                                     |
| Zoom                                 | Not implemented                   | --                                     |
| Speaker accuracy (WER < 15%)         | Needs human speech, not just TTS  | tests2 rt-replay proc                  |
| K8s profile propagation              | Covered by K8S_DEPLOYMENTS_READY + K8S_NO_CRASHLOOP | `make helm-smoke`  |

## Relationship to tests2

tests2 is agent-interpreted markdown procs. tests3 is executable code.

- tests2 procs are useful for **discovery** -- debugging, finding new bugs, evaluating transcription quality
- tests3 checks are useful for **enforcement** -- preventing known bugs from returning, validating deployments

The workflow: agent runs tests2 procs, finds a bug, fixes code, adds a tests3 registry entry. The bug never comes back.

|          | tests2                         | tests3                |
| -------- | ------------------------------ | --------------------- |
| Format   | Markdown procs                 | Shell/Python scripts  |
| Runs via | Agent interprets               | `make` targets        |
| Speed    | Minutes-hours                  | Seconds-minutes       |
| Purpose  | Discovery, deep validation     | Regression prevention |
| When     | Before release, debug sessions | Every code change, CI |

## Data collection

### Why collect

Regression checks validate that the system works. Data collection measures **how well** it works -- speaker attribution accuracy, word error rate, segment completeness. You can't improve transcription quality without ground-truth datasets to score against.

### What a collection run produces

A collection run is a live meeting where known utterances are played via TTS and the pipeline's output is captured alongside the input:

```
testdata/gmeet-compose-260405/
├── ground-truth.json          # input: [{speaker, text, delay_ms}, ...]
└── pipeline/
    ├── rest-segments.json     # what the REST API returned
    ├── db-segments.csv        # what Postgres stored
    └── score.json             # quality metrics
```

**Ground truth** = scripted utterances with speaker labels and timing. **Raw output** = what the transcription pipeline actually produced. **Score** = how closely output matched input (speaker accuracy, text similarity, completeness, hallucination count).

### How it works today

Collection currently lives in `tests/` as agent-interpreted markdown procs (`tests/rt-collection.md`):

1. Host a live meeting (GMeet or Teams)
2. Launch a recorder bot (captures audio, runs transcription)
3. Launch speaker bots (one per speaker in ground truth)
4. Send TTS utterances via `POST /bots/{platform}/{id}/speak` with timed delays
5. Wait for the transcription pipeline to process
6. Capture REST API response + Postgres export
7. Score with `rt-score.py` (WER, speaker accuracy, completeness)
8. Stop all bots, save dataset to `tests/testdata/{platform}-{mode}-{date}/`

Existing datasets:
- `tests/testdata/gmeet-compose-260405/` -- 9 utterances, 3 speakers, 92% text similarity, 100% speaker accuracy
- `tests/testdata/teams-compose-260405/` -- 21 utterances, more complex multi-speaker

### What tests3 already has

`tests3/tests/transcribe.sh` runs a minimal collection: 3 TTS utterances, fetch transcript, count segments, check for ground-truth phrases. It proves the pipeline works but doesn't save datasets or compute quality metrics.

### What needs to migrate

The full collection workflow should move from agent-interpreted procs to executable scripts in tests3:

| Capability                     | Current home           | Target                         |
| ------------------------------ | ---------------------- | ------------------------------ |
| Multi-speaker TTS orchestration | `tests/rt-collection.md` | `tests3/tests/collect.sh`      |
| Ground-truth conversation files | `tests/testdata/*.json`  | `tests3/testdata/`             |
| Pipeline capture (REST + DB)    | `tests/rt-collection.md` | `tests3/tests/collect.sh`      |
| Offline scoring                 | `tests/rt-replay.md`    | `tests3/tests/score.sh`        |
| WS/REST consistency             | `tests/rt-delivery.md`  | `tests3/tests/delivery.sh`     |
| Dataset corpus                  | `tests/testdata/`        | `tests3/testdata/`             |

The goal: `make -C tests3 collect` runs a full collection session and saves a scored dataset. `make -C tests3 score DATASET=gmeet-compose-260405` re-scores an existing dataset offline. No agent interpretation required.
