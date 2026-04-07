# Proc Graph

> Every proc is a node. Edges are data: proc A `gives:` what proc B `needs:`.
> Any node can be skipped if its outputs already exist in state.
> Cookbooks are paths through this graph.

## Full graph

```
═══════════════════════════════════════════════════════════════════════════════
STATE: —
═══════════════════════════════════════════════════════════════════════════════

  infra ─────────────────────────────────────────────────────────────────┐
    gives: GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE,            │
           DASHBOARD_URL                                                 │
                                                                         │
═══════════════════════════════════════════════════════════════════════════════
STATE: GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL
═══════════════════════════════════════════════════════════════════════════════

  api ───────────────────────────────────────────────────────────────────┐
    gives: USER_ID, API_TOKEN                                            │
                                                                         │
═══════════════════════════════════════════════════════════════════════════════
STATE: + USER_ID, API_TOKEN
═══════════════════════════════════════════════════════════════════════════════

  ┌─── no meeting needed ──────────────────────────────────┐
  │                                                         │
  │  urls ──────────────── gives: TEAMS_URLS_OK             │
  │  dashboard ─────────── gives: DASHBOARD_OK              │
  │  websocket ─────────── gives: WEBSOCKET_OK              │
  │    tier 1: protocol (no meeting)                        │
  │    tier 2: content delivery (needs active meeting)      │
  │    debug: redis channel inspection (isolate pub vs relay)│
  │  webhooks ──────────── gives: WEBHOOK_OK                │
  │  containers ────────── gives: LIFECYCLE_OK              │
  │  analytics ─────────── gives: ANALYTICS_OK        (new) │
  │  scheduler ─────────── gives: SCHEDULER_OK        (new) │
  │  agent-chat ────────── gives: AGENT_OK            (new) │
  │  recording-config ──── gives: REC_CONFIG_OK       (new) │
  │  calendar ──────────── gives: CALENDAR_OK  [human](new) │
  │                                                         │
  │  all independent — run any subset, any order            │
  └─────────────────────────────────────────────────────────┘

  ┌─── meeting chain ──────────────────────────────────────┐
  │                                                         │
  │  browser ─────────────────────────────────────────┐     │
  │    gives: SESSION_TOKEN, CDP_URL, SAVED_STATE     │     │
  │    [human: login]                                  │     │
  │                                                    │     │
  ╞════════════════════════════════════════════════════════════
  │  STATE: + SESSION_TOKEN, CDP_URL                   │     │
  ╞════════════════════════════════════════════════════════════
  │                                                    │     │
  │  meeting(PLATFORM) ───────────────────────────┐    │     │
  │    gives: MEETING_URL, NATIVE_MEETING_ID      │    │     │
  │    [human: teams URL]                          │    │     │
  │                                                │    │     │
  ╞════════════════════════════════════════════════════════════
  │  STATE: + MEETING_URL, NATIVE_MEETING_ID       │    │     │
  ╞════════════════════════════════════════════════════════════
  │                                                │    │     │
  │  bot ─────────────────────────────────────┐    │    │     │
  │    gives: RECORDER_ID, BOT_STATUS         │    │    │     │
  │    [human: admit]                          │    │    │     │
  │                                            │    │    │     │
  │  admit ────────────────────────────────┐   │    │    │     │
  │    gives: BOT_ADMITTED                 │   │    │    │     │
  │                                        │   │    │    │     │
  ╞════════════════════════════════════════════════════════════
  │  STATE: + RECORDER_ID, BOT_ADMITTED    │   │    │    │     │
  │  (bot is active in meeting)            │   │    │    │     │
  ╞════════════════════════════════════════════════════════════
  │                                        │   │    │    │     │
  │  ┌── while bot active ──────────┐      │   │    │    │     │
  │  │ transcription                │      │   │    │    │     │
  │  │   gives: TRANSCRIPT_SEGMENTS,│      │   │    │    │     │
  │  │          WER, CHAT_OK        │      │   │    │    │     │
  │  │ screen-share           (new) │      │   │    │    │     │
  │  │   gives: SCREEN_OK          │      │   │    │    │     │
  │  │ bot-config             (new) │      │   │    │    │     │
  │  │   gives: CONFIG_OK          │      │   │    │    │     │
  │  └──────────────────────────────┘      │   │    │    │     │
  │                                        │   │    │    │     │
  ╞════════════════════════════════════════════════════════════
  │  STATE: + TRANSCRIPT_SEGMENTS          │   │    │    │     │
  │  (bot still active or being stopped)   │   │    │    │     │
  ╞════════════════════════════════════════════════════════════
  │                                        │   │    │    │     │
  │  finalize ──────────────────────────┐  │   │    │    │     │
  │    gives: FINALIZATION_OK           │  │   │    │    │     │
  │                                     │  │   │    │    │     │
  ╞════════════════════════════════════════════════════════════
  │  STATE: + FINALIZATION_OK           │  │   │    │    │     │
  │  (meeting ended, bots stopped)      │  │   │    │    │     │
  ╞════════════════════════════════════════════════════════════
  │                                     │  │   │    │    │     │
  │  post-meeting ──── gives: POST_MEETING_SEGMENTS       │   │
  │  recordings ─────── gives: RECORDINGS_OK        (new) │   │
  │  sharing ────────── gives: SHARING_OK           (new) │   │
  │                                                        │   │
  └────────────────────────────────────────────────────────┘

  ┌─── rt chain ───────────────────────────────────────────┐
  │                                                         │
  │  rt-collect ──── gives: DATASET_PATH                    │
  │  rt-replay ───── gives: WER, SPEAKER_ACCURACY           │
  │  rt-delivery ─── gives: DELIVERY_OK                     │
  │                                                         │
  └─────────────────────────────────────────────────────────┘

  ┌─── standalone ─────────────────────────────────────────┐
  │                                                         │
  │  deploy ──── gives: DEPLOY_METHODS, ALL_GAPS            │
  │  (uses VMs, no running infra needed)                    │
  │                                                         │
  └─────────────────────────────────────────────────────────┘
```

## Cookbook paths

Each cookbook is a path through the graph. `ensure:` skips nodes whose
outputs already exist. Highlighted nodes are what the cookbook actually runs.

### smoke
```
ensure: GATEWAY_URL, ADMIN_TOKEN  from: infra
ensure: API_TOKEN                 from: api

 ▶ urls
 ▶ dashboard
 ▶ websocket
```

### protocols
```
ensure: GATEWAY_URL, ADMIN_TOKEN  from: infra
ensure: API_TOKEN                 from: api

parallel:
   ▶ websocket
   ▶ webhooks
   ▶ containers
```

### agent
```
ensure: GATEWAY_URL, ADMIN_TOKEN  from: infra
ensure: API_TOKEN                 from: api

 ▶ agent-chat
 ▶ scheduler
```

### meeting-e2e (PLATFORM=google_meet)
```
ensure: GATEWAY_URL, ADMIN_TOKEN  from: infra
ensure: API_TOKEN                 from: api
ensure: SESSION_TOKEN             from: browser         [human: login]
ensure: MEETING_URL               from: meeting
ensure: BOT_ADMITTED              from: bot + admit     [human: admit]

 ▶ transcription                                        [human: admit speaker]
 ▶ finalize
 ▶ post-meeting
 ▶ recordings
 ▶ sharing
```

### full-stack
```
ensure: GATEWAY_URL, ADMIN_TOKEN  from: infra
ensure: API_TOKEN                 from: api

 ▶ urls
 ▶ dashboard

ensure: SESSION_TOKEN             from: browser         [human: login]

parallel:
   branch gmeet:
       ensure: MEETING_URL        from: meeting(google_meet)
       ensure: BOT_ADMITTED       from: bot + admit     [human: admit]
        ▶ transcription
        ▶ screen-share
        ▶ bot-config
        ▶ finalize → post-meeting → recordings → sharing

   branch teams:
       ensure: MEETING_URL        from: meeting(teams)  [human: URL]
       ensure: BOT_ADMITTED       from: bot + admit     [human: admit]
        ▶ transcription
        ▶ finalize → post-meeting → recordings → sharing

parallel:
    ▶ websocket
    ▶ webhooks
    ▶ containers
    ▶ analytics
    ▶ agent-chat
    ▶ scheduler

 ▶ score → CONFIDENCE
```

### rt-quality
```
ensure: GATEWAY_URL, API_TOKEN    from: infra + api
ensure: DATASET_PATH              from: rt-collect      [human: admit bots]

 ▶ rt-replay
 ▶ rt-delivery
```

### deploy
```
(no ensure — standalone)

 ▶ deploy                                               [human: approve fixes]
```

### media
```
ensure: GATEWAY_URL, API_TOKEN    from: infra + api
ensure: MEETING_URL               from: browser + meeting
ensure: BOT_ADMITTED              from: bot + admit
ensure: FINALIZATION_OK           from: transcription + finalize

 ▶ post-meeting
 ▶ recordings
 ▶ sharing
```

## State accumulation — full-stack example

```
start:    {}
infra:    + GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL
api:      + USER_ID, API_TOKEN
urls:     + TEAMS_URLS_OK
dashboard:+ DASHBOARD_OK
browser:  + SESSION_TOKEN, CDP_URL, SAVED_STATE
meeting:  + MEETING_URL, NATIVE_MEETING_ID (×2 for gmeet + teams)
bot:      + RECORDER_ID, BOT_STATUS (×2)
admit:    + BOT_ADMITTED (×2)
transcr:  + TRANSCRIPT_SEGMENTS, WER, SPEAKER_ACCURACY, CHAT_OK (×2)
finalize: + FINALIZATION_OK (×2)
post-mtg: + POST_MEETING_SEGMENTS, RECORDING_UPLOADED (×2)
records:  + RECORDINGS_OK (×2)
sharing:  + SHARING_OK (×2)
websocket:+ WEBSOCKET_OK
webhooks: + WEBHOOK_OK
containers:+ LIFECYCLE_OK, ORPHAN_COUNT, ZOMBIE_COUNT
analytics:+ ANALYTICS_OK
agent:    + AGENT_OK
scheduler:+ SCHEDULER_OK
score:    → CONFIDENCE = weighted sum of all *_OK values
```

## Human gates

These procs cannot be fully automated. They block until human acts:

| Proc | Human action | When |
|---|---|---|
| browser | Log into Google/GitHub via VNC | Once, saved to MinIO |
| meeting(teams) | Paste Teams meeting URL | Each Teams meeting |
| bot | Admit recorder in meeting UI | Each bot, or use `admit` proc |
| transcription | Admit speaker bot | Each speaker bot |
| calendar | Complete OAuth flow | Once |
| deploy | Approve each doc fix | Each gap found |
