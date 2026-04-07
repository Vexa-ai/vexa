# Inventory

> **Procs own features.** A feature says "here's what should be true" — its DoD
> table lists checks, weights, and confidence thresholds. A proc executes against
> reality, updates the feature's DoD with evidence (PASS/FAIL, date, proof), and
> computes the confidence score. Features are the honest report card. Procs are
> the exam. Together they answer: **what is the actual delivery status?**
>
> **Procs also own the feature README itself.** After every run, the proc
> verifies the feature doc is accurate. If reality differs, the proc fixes it.
>
> **Confidence is calculated, never claimed.**

## Composition model

Every proc is a function: `needs → [steps] → gives`.
Composition is data flow: proc A gives X, proc B needs X.

**State can already exist.** If `needs:` are satisfied — from `env.md`, from a
running deployment, from a previous run — skip the proc that produces them.
A cookbook resolves missing state, not a fixed sequence.

```
cookbook wants to run: transcription
    transcription needs: GATEWAY_URL, API_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID

    GATEWAY_URL exists in env?  → use it, skip infra
    API_TOKEN exists in env?    → use it, skip api
    NATIVE_MEETING_ID missing?  → need meeting → need browser → run those
```

Like `make`: don't rebuild targets that are up to date.

## Proc graph — needs / gives

### Base (produce credentials)

```
infra                needs: —
                     gives: GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL

api                  needs: GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN
                     gives: USER_ID, API_TOKEN
```

### No-meeting procs (need credentials only)

```
urls                 needs: GATEWAY_URL, API_TOKEN
                     gives: TEAMS_URLS_OK

dashboard            needs: GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DASHBOARD_URL, DEPLOY_MODE, API_TOKEN
                     gives: DASHBOARD_OK

websocket            needs: GATEWAY_URL, API_TOKEN
                     gives: WEBSOCKET_OK
                     tier 1: protocol (no meeting)
                     tier 2: content + debug (needs MEETING_PLATFORM, NATIVE_MEETING_ID)

webhooks             needs: GATEWAY_URL, API_TOKEN, DEPLOY_MODE
                     gives: WEBHOOK_OK

containers           needs: GATEWAY_URL, API_TOKEN, DEPLOY_MODE
                     gives: LIFECYCLE_OK, ORPHAN_COUNT, ZOMBIE_COUNT

analytics            needs: ADMIN_URL, ADMIN_TOKEN                                        ← NEW
                     gives: ANALYTICS_OK

scheduler            needs: GATEWAY_URL, API_TOKEN                                        ← NEW
                     gives: SCHEDULER_OK

agent-chat           needs: GATEWAY_URL, API_TOKEN                                        ← NEW
                     gives: AGENT_OK

recording-config     needs: GATEWAY_URL, API_TOKEN                                        ← NEW
                     gives: REC_CONFIG_OK

calendar             needs: GATEWAY_URL, API_TOKEN [human: OAuth]                         ← NEW
                     gives: CALENDAR_OK
```

### Meeting chain (need a live meeting)

```
browser              needs: GATEWAY_URL, API_TOKEN, DASHBOARD_URL, USER_ID
                     gives: SESSION_TOKEN, CDP_URL, SAVED_STATE
                     tier 1: auto (S3 roundtrip, no login)
                     tier 2: [human: Google login] → verify persistence

meeting              needs: GATEWAY_URL, API_TOKEN, MEETING_PLATFORM (+ SESSION_TOKEN for gmeet)
                     gives: MEETING_URL, NATIVE_MEETING_ID

bot                  needs: GATEWAY_URL, API_TOKEN, MEETING_URL, MEETING_PLATFORM, NATIVE_MEETING_ID
                     gives: RECORDER_ID, BOT_STATUS
                     [human: admit]

admit                needs: GATEWAY_URL, API_TOKEN, SESSION_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID
                     gives: BOT_ADMITTED
```

### Active-bot procs (need bot in meeting)

```
transcription        needs: GATEWAY_URL, API_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID
                     gives: TRANSCRIPT_SEGMENTS, WER, SPEAKER_ACCURACY, CHAT_OK
                     [human: admit speaker]

screen-share         needs: GATEWAY_URL, API_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID   ← NEW
                     gives: SCREEN_OK

bot-config           needs: GATEWAY_URL, API_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID   ← NEW
                     gives: CONFIG_OK
```

### Post-meeting procs (need meeting ended)

```
finalize             needs: GATEWAY_URL, API_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID
                     gives: FINALIZATION_OK

post-meeting         needs: GATEWAY_URL, API_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID
                     gives: POST_MEETING_SEGMENTS, RECORDING_UPLOADED

recordings           needs: GATEWAY_URL, API_TOKEN, MEETING_ID                            ← NEW
                     gives: RECORDINGS_OK

sharing              needs: GATEWAY_URL, API_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID   ← NEW
                     gives: SHARING_OK
```

### Offline procs (need dataset, no live infra)

```
rt-collect           needs: GATEWAY_URL, API_TOKEN, MEETING_PLATFORM
                     gives: DATASET_PATH, GROUND_TRUTH_COUNT

rt-replay            needs: DATASET_PATH
                     gives: WER, SPEAKER_ACCURACY, COMPLETENESS

rt-delivery          needs: GATEWAY_URL, API_TOKEN, DATASET_PATH, MEETING_PLATFORM, NATIVE_MEETING_ID
                     gives: DELIVERY_OK, WS_REST_MATCH, PHANTOM_COUNT
```

### Standalone

```
deploy               needs: — (uses VMs, not running infra)
                     gives: DEPLOY_METHODS, ALL_GAPS
```

## Dependency resolution

```
                                                     ┌─ urls
                                                     ├─ dashboard
                                                     ├─ websocket
                                                     ├─ webhooks
                                                     ├─ containers
                  ┌─────────────────── (credentials) ┤
                  │                                   ├─ analytics        (new)
infra ──→ api ────┤                                   ├─ scheduler        (new)
                  │                                   ├─ agent-chat       (new)
                  │                                   ├─ recording-config (new)
                  │                                   └─ calendar         (new)
                  │
                  │                                   ┌─ transcription
                  └── browser ──→ meeting ──→ bot ────┤  screen-share    (new)
                                                      │  bot-config      (new)
                                                      │
                                                      └─ finalize ──→ post-meeting
                                                                     ├─ recordings  (new)
                                                                     └─ sharing     (new)

deploy (standalone)

rt-collect ──→ rt-replay ──→ rt-delivery
```

Any node can be skipped if its `gives:` are already in state.

## Cookbooks — paths through the graph

A cookbook declares what it wants to validate. State resolution fills in the chain.

| Cookbook | Target procs | Skips if you already have... |
|---|---|---|
| **smoke** | urls, dashboard, websocket | GATEWAY_URL + API_TOKEN → skip infra, api |
| **protocols** | websocket, webhooks, containers | same |
| **agent** | agent-chat, scheduler | same |
| **meeting-e2e** | browser → ... → sharing | SESSION_TOKEN → skip browser; MEETING_URL → skip meeting |
| **full-stack** | everything | nothing (fresh) or everything resolved piecemeal |
| **deploy** | deploy | nothing (standalone) |
| **rt-quality** | rt-replay, rt-delivery | DATASET_PATH → skip rt-collect |
| **media** | recordings, sharing | MEETING_ID + FINALIZATION_OK → skip entire meeting chain |

## Missing libs

| Lib | Functions | Eliminates duplication in |
|---|---|---|
| **lib/browser** | `connect_cdp`, `navigate`, `click`, `wait_for` | browser, meeting, admit, screen-share |
| **lib/redis** | `publish`, `subscribe`, `get` | bot commands, chat, session mgmt |
| **lib/storage** | `list`, `exists`, `download` | recordings, browser state, workspaces |
| **lib/sse** | `stream`, `collect_events` | agent-chat |

## Feature docs

| Feature doc | Proc that owns it | Status |
|---|---|---|
| features/browser-session/ | browser | created |
| features/recordings/ | recordings | missing |
| features/transcript-sharing/ | sharing | missing |
| features/agent-chat/ | agent-chat | missing |
| features/calendar/ | calendar | missing |
| features/screen-share/ | screen-share | missing |
| features/analytics/ | analytics | missing |
| features/scheduler/ | scheduler | missing |
| features/dashboard/ | dashboard | created |
