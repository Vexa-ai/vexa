# RT Test Graph — Compose × GMeet + Teams → 90

> Follows [RULES.md](../RULES.md). Nodes own their scripts and fix what doesn't work.

Target: confidence ≥ 90 for all features on docker compose.

## Features covered

| Feature | Validated by nodes |
|---------|--------------------|
| realtime-transcription | 07, 09, 12 |
| post-meeting-transcription | 10 |
| remote-browser | 05-browser-session, 04-dashboard |
| speaking-bot | 07, 09 |
| meeting-chat | 12 |
| webhooks | 13 |
| auth-and-limits | 02-api, 07 |
| bot-lifecycle | 07, 14-container-lifecycle |
| container-lifecycle | 14-container-lifecycle |
| meeting-urls | 03-url-formats |

## Graph

```
                                                                    ┌→ 06(gmeet) → 07 → 09 → 10 ─┐
01-infra → 02-api → 03-urls → 04-dash → 05-browser ───────────────┤                              ├→ 11 → 12 → 13 → 14-containers → REPORT
                                                                    └→ 06(teams) → 07 → 09 → 10 ─┘
beta ──────────────────────────────────────────────────────────────────────────────────────────────────── (continuous)
```

Shared setup → URL parsing → dashboard → browser session → parallel meeting branches → delivery → webhooks → container cleanup.

**beta** runs continuously alongside all other teammates — see [Team execution](#team-execution).

## Team execution

Run this graph with `TeamCreate("full-stack-run")`. The conductor creates tasks with `blockedBy` matching the graph arrows, then spawns teammates.

### Conductor rules

The conductor (team-lead) **coordinates only — does not execute**:
- Does NOT run curl commands, launch bots, or create users
- Relays outputs between teammates (GATEWAY_URL, API_TOKEN, meeting URLs)
- Relays human-provided inputs (meeting URLs, admission confirmations)
- Flags blockers and reassigns work
- Delegates all code changes, procedure fixes, and debugging to teammates

### Teammate rules

All teammates MUST follow these in addition to [RULES.md](../RULES.md):

1. **No code patches inside running containers.** Never `docker exec` to edit
   source code, config, or env vars inside the `vexa` container. Fix in the
   REPO, rebuild the image. Container-only patches create false positives and
   mask the true fix location.

2. **No silent waits.** When waiting for external events (bot admission, human
   action, service startup), poll status every 5-10s and log each state change.
   Never `sleep 120` and hope. Log "Bot X awaiting admission — human needs to
   admit" so the conductor can relay to the human.

3. **One rebuild, then freeze.** The conductor decides when to rebuild. Once the
   container is running with active bots, DO NOT rebuild. Request the conductor
   to schedule a rebuild at a safe point (no active meetings/bots).

4. **Use the meeting URL you're given.** When the conductor relays a meeting URL,
   use it exactly. Do not create browser sessions to navigate to meetings when
   the URL is already provided. Browser sessions are ONLY for GMeet (meet.new
   requires Google login). Teams meetings always come as URLs from the human.

5. **Create your own users/tokens.** Each branch needs separate users for
   multi-bot tests (one-bot-per-user-per-meeting limit). Create them via the
   admin API yourself. Don't wait for infra to do it.

6. **Bot admission timeout.** Set `no_one_joined_timeout` to 300000 (5 min) in
   bot creation payload for tests — the default 120s is too short when humans
   need to context-switch to admit bots.

### Teammates

| Name | Type | Tasks | Role |
|------|------|-------|------|
| infra | general-purpose | 01-05 (sequential) | Shared setup. Runs scripts, outputs GATEWAY_URL, API_TOKEN, etc. Also owns procedure/test fixes. |
| gmeet | general-purpose | 06-10 GMeet branch | Creates meeting, launches bots, verifies transcription. Owns user creation for TTS bots. |
| teams | general-purpose | 06-10 Teams branch | Same as gmeet, parallel. Receives meeting URL from conductor (human provides it). Owns user creation for TTS bots. |
| post | general-purpose | 11-14 + REPORT | Finalization, WS, webhooks, containers, final confidence. |
| docs | general-purpose | No numbered tasks | Reads test-log.md continuously. Updates feature READMEs as results come in — DoD tables, Status, Evidence, Last checked, confidence scores. Does NOT wait for REPORT. |
| beta | general-purpose | No owned tasks | Pair programmer + rule enforcer. Flags container patches, silent waits, wrong URLs, unlogged work. |

### Task graph with blockedBy

```
Task 1:  01-infra-up                    blockedBy: []           owner: infra
Task 2:  02-api                         blockedBy: [1]          owner: infra
Task 3:  03-url-formats                 blockedBy: [2]          owner: infra
Task 4:  04-dashboard                   blockedBy: [1]          owner: infra
Task 5:  05-browser-session             blockedBy: [2]          owner: infra
Task 6a: 06-create-meeting-gmeet        blockedBy: [5]          owner: gmeet
Task 6b: 06-create-meeting-teams        blockedBy: [5]          owner: teams
Task 7a: 07-bot-lifecycle-gmeet         blockedBy: [6a]         owner: gmeet
Task 7b: 07-bot-lifecycle-teams         blockedBy: [6b]         owner: teams
Task 8a: 09-verify-transcription-gmeet  blockedBy: [7a]         owner: gmeet
Task 8b: 09-verify-transcription-teams  blockedBy: [7b]         owner: teams
Task 9a: 10-verify-post-meeting-gmeet   blockedBy: [8a]         owner: gmeet
Task 9b: 10-verify-post-meeting-teams   blockedBy: [8b]         owner: teams
Task 10: 11-finalization                blockedBy: [9a, 9b]     owner: post
Task 11: 12-websocket                   blockedBy: [10]         owner: post
Task 12: 13-webhooks                    blockedBy: [10]         owner: post
Task 13: 14-container-lifecycle         blockedBy: [10]         owner: post
Task 14: REPORT                         blockedBy: [11, 12, 13] owner: post
```

### beta — pair programmer + rule enforcer

beta has no owned tasks. It runs continuously alongside all teammates:

1. Reads the same procedure `.md` the active teammate is executing
2. Watches `test-log.md` entries as they appear
3. Independently verifies claims — queries DB, checks REST output, reads container logs
4. Messages the teammate directly when it finds:
   - Workarounds disguised as fixes (switching endpoints instead of fixing the broken one)
   - PASS without observable evidence
   - Docs updated with claims the test didn't prove
   - Ground truth evaluation skipped
   - Confidence inflation
   - **Container code patches** (`docker exec` editing source files inside the container)
   - **Silent waits** (sleeping without polling/logging status)
   - **Wrong meeting URLs** (using stale URLs after conductor relayed new ones)
   - **Unlogged work** (completed tasks with no test-log.md entries)
   - **Unnecessary rebuilds** (rebuilding while bots/meetings are active)
5. Creates tasks for real software bugs it discovers

beta does not block anything. It speaks up in real time.

### docs — continuous documentation updater

docs has no numbered tasks. It runs continuously alongside all teammates:

1. Watches `test-log.md` for new PASS/FAIL/FINDING entries
2. For each entry, reads the corresponding procedure's `docs:` frontmatter to find which READMEs to update
3. Reads the procedure's `## Docs ownership` section for specific claims to verify
4. Updates feature READMEs immediately:
   - DoD table: Status → PASS/FAIL, Evidence → one-line summary, Last checked → ISO 8601
   - Confidence score: recalculate from DoD weights
   - Components table: verify file paths still exist
   - API examples: update curl commands, response shapes if reality differs
5. Tracks which docs are updated and which are pending

docs does NOT wait for REPORT. It updates as results arrive. REPORT (task 18)
then verifies completeness — were all docs updated? Any gaps?

**Why a separate teammate:** Execution teammates (gmeet, teams, infra) are
focused on running tests and fixing bugs. Asking them to also update 10+
READMEs after each step splits attention and delays the pipeline. The docs
teammate reads their results and handles documentation in parallel.

### Docs ownership flow

Each procedure declares `docs:` in frontmatter (which files) and
`## Docs ownership` (which specific claims). The docs teammate reads both
and updates the files. Execution teammates do NOT update docs — they log
results, docs picks them up.

## Execution

### Shared (sequential)

| Step | Node | Inputs | Outputs | Features |
|------|------|--------|---------|----------|
| 1 | [01-infra-up](../01-infra-up.md) → [01a-compose](../01a-infra-compose.md) or [01b-lite](../01b-infra-lite.md) | — | GATEWAY_URL, ADMIN_TOKEN | all |
| 2 | [02-api](../02-api.md) | GATEWAY_URL, ADMIN_TOKEN | API_TOKEN, USER_ID | auth-and-limits |
| 3 | [03-url-formats](../03-url-formats.md) | GATEWAY_URL, API_TOKEN | TEAMS_URLS_OK | meeting-urls |
| 4 | [04-dashboard](../04-dashboard.md) | — | DASHBOARD_URL | remote-browser, auth-and-limits |
| 5 | [05-browser-session](../05-browser-session.md) | API_TOKEN | SESSION_TOKEN, CDP_URL | remote-browser |

### GMeet branch (parallel with Teams)

| Step | Node | Inputs | Outputs | Features |
|------|------|--------|---------|----------|
| 6a | [06-create-meeting](../06-create-meeting.md) | API_TOKEN, PLATFORM=google_meet, CDP_URL | MEETING_URL, NATIVE_MEETING_ID | meeting-urls |
| 6b | [07-bot-lifecycle](../07-bot-lifecycle.md) | API_TOKEN, MEETING_URL, SPEAKERS=3 | RECORDER_ID, SPEAKER_BOTS | bot-lifecycle, speaking-bot, auth-and-limits |
| 6c | [09-verify-transcription](../09-verify-transcription.md) | API_TOKEN, NATIVE_MEETING_ID, SPEAKERS=3 | TRANSCRIPT_COUNT, SPEAKER_COUNT | realtime-transcription, speaking-bot |
| 6d | [10-verify-post-meeting](../10-verify-post-meeting.md) | API_TOKEN, NATIVE_MEETING_ID, MEETING_ID | POST_MEETING_SEGMENTS | post-meeting-transcription |

### Teams branch (parallel with GMeet)

| Step | Node | Inputs | Outputs | Features |
|------|------|--------|---------|----------|
| 7a | [06-create-meeting](../06-create-meeting.md) | API_TOKEN, PLATFORM=teams, PASSCODE | MEETING_URL, NATIVE_MEETING_ID | meeting-urls |
| 7b | [07-bot-lifecycle](../07-bot-lifecycle.md) | API_TOKEN, MEETING_URL, SPEAKERS=3 | RECORDER_ID, SPEAKER_BOTS | bot-lifecycle, speaking-bot, auth-and-limits |
| 7c | [09-verify-transcription](../09-verify-transcription.md) | API_TOKEN, NATIVE_MEETING_ID, SPEAKERS=3 | TRANSCRIPT_COUNT, SPEAKER_COUNT | realtime-transcription, speaking-bot |
| 7d | [10-verify-post-meeting](../10-verify-post-meeting.md) | API_TOKEN, NATIVE_MEETING_ID, MEETING_ID | POST_MEETING_SEGMENTS | post-meeting-transcription |

### After both branches (sequential)

| Step | Node | Inputs | Outputs | Features |
|------|------|--------|---------|----------|
| 8 | [11-finalization](../11-finalization.md) | API_TOKEN, all MEETING_IDs | FINALIZATION_OK | bot-lifecycle |
| 9 | [12-websocket](../12-websocket.md) | API_TOKEN, NATIVE_MEETING_ID | WS_OK | realtime-transcription, meeting-chat |
| 10 | [13-webhooks](../13-webhooks.md) | API_TOKEN | WEBHOOKS_OK | webhooks |
| 11 | [14-container-lifecycle](../14-container-lifecycle.md) | — | ORPHANS=0 | container-lifecycle, bot-lifecycle |

### REPORT

After all nodes complete:

1. **Read `test-log.md`** — collect every PASS/FAIL/FIX entry from this run.

2. **Match results to DoD items** — each node declares which features it proves
   (the Features column in the tables above). Map each PASS/FAIL to the specific
   DoD item(s) in the feature README.

3. **Update feature READMEs** — for each DoD item proved by a node that ran:
   - `Status` → PASS or FAIL
   - `Evidence` → one-line summary (e.g. "12 segments, 4 speakers")
   - `Last checked` → today's date

4. **Compute confidence per feature** — from the updated DoD table
   (ceiling logic, weighted sum).

5. **Compute overall confidence** — from the graph's "What 90 requires" table.

6. **Append run summary to `test-log.md`**:
   ```
   REPORT — rt-graph — confidence=X
     realtime-transcription: Y
     post-meeting-transcription: Y
     ...per feature
   ```

The feature READMEs are the durable record. `test-log.md` is the audit trail.
Nodes log during execution. REPORT writes results back to features after.

## 3-speaker setup

Each branch sends 4 bots total:
- 1 recorder bot (transcribe_enabled: true)
- 3 TTS speaker bots (Alice, Bob, Charlie) using /speak endpoint

The 3 speakers provide ground truth for speaker attribution.

## What 90 requires

| # | Check | Weight | Ceiling | Node | Feature |
|---|-------|--------|---------|------|---------|
| 1 | GMeet: bot joins + realtime transcription delivered | 10 | ceiling | 07 + 09 | realtime-transcription |
| 2 | Teams: bot joins + realtime transcription delivered | 10 | ceiling | 07 + 09 | realtime-transcription |
| 3 | GMeet: ≥3 speakers attributed correctly | 8 | ceiling | 09 | realtime-transcription |
| 4 | Teams: ≥3 speakers attributed correctly | 8 | ceiling | 09 | realtime-transcription |
| 5 | GMeet: post-meeting transcription with speakers | 6 | — | 10 | post-meeting-transcription |
| 6 | Teams: post-meeting transcription with speakers | 6 | — | 10 | post-meeting-transcription |
| 7 | Browser session persists login across sessions | 6 | — | 05-browser-session | remote-browser |
| 8 | TTS speech heard by other participants | 6 | — | 09 | speaking-bot |
| 9 | Dashboard shows transcript | 5 | — | 04-dashboard | remote-browser |
| 10 | Teams URL formats parsed (T1-T6) | 5 | — | 03-urls | meeting-urls |
| 11 | Auth: invalid token rejected, scopes enforced | 5 | — | 02-api | auth-and-limits |
| 12 | WS delivery matches REST | 5 | — | 12 | realtime-transcription |
| 13 | Webhooks fire on meeting end | 5 | — | 13 | webhooks |
| 14 | No orphan containers after test | 5 | — | 14-containers | container-lifecycle |
| 15 | Bot lifecycle: requested → active → completed | 5 | — | 07 | bot-lifecycle |
| 16 | Meeting chat read/write | 5 | — | 09 | meeting-chat |

Total weight: 100. If any ceiling FAILS → confidence = 0.

## Confidence

```
for each ceiling: if FAIL → 0
score = sum(passing_weight) / sum(total_weight) × 100
```
