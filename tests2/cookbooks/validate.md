---
needs: []
gives: [CONFIDENCE]
---

use: env
use: lib/log

# Validate

> **Why:** One cookbook to validate both compose (local dev) and lite (VM) deployments. Replaces the monolithic full-stack cookbook with tiered execution that stops at the first broken layer.
> **What:** 5 tiers, each gating the next. Tier 0 (preflight) catches dead infra and stale credentials in seconds. Tier 1 (smoke) validates all APIs without meetings. Tier 2 (auth) tests the full login→data chain. Tier 3 (meetings) runs live GMeet + Teams with bots. Tier 4 (render) loads the dashboard in a browser and checks what the user sees.
> **How:** Calls the same src/ procs as full-stack but in tier order. Procs branch on DEPLOY_MODE internally. Each tier has a gate — if it fails, skip downstream tiers (they'd fail too). Score computed from DoD weights across all feature docs.

## state

    DEPLOY_TARGET = ""   # "compose" or "lite" — passed by caller
    VM_IP         = ""
    VM_ID         = ""
    SSH           = ""   # ssh command prefix for VM commands
    CONFIDENCE    = 0
    TIER_REACHED  = -1

## steps

```
0. init
   call: log.init(COOKBOOK="validate")
   => LOG_FILE, JSONL_FILE

   > DEPLOY_TARGET must be passed: "compose" or "lite".
   > BRANCH must be passed: which git branch/commit to validate.
   > Both run on a fresh VM — no local validation, no "works on my machine".
   if DEPLOY_TARGET not in ["compose", "lite"]:
       ask: "Deploy target? (compose / lite)"
       => DEPLOY_TARGET
   if BRANCH is empty:
       do: git rev-parse --abbrev-ref HEAD
       => BRANCH
       ask: "Branch to validate? (default: {BRANCH})"
   on_fail: stop

═══════════════════════════════════════════════════════════════
 TIER -1 — Provision + Preflight (~5 min)
═══════════════════════════════════════════════════════════════

1. provision
   call: src/provision(DEPLOY_TARGET={DEPLOY_TARGET}, BRANCH={BRANCH})
   => VM_IP, VM_ID, SSH, K8S_NAMESPACE, GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL, USER_ID, API_TOKEN
   on_fail: stop

=> TIER_REACHED = 0
emit PASS "tier 0: provisioned + preflight ({DEPLOY_MODE})"

═══════════════════════════════════════════════════════════════
 TIER 1 — Smoke (5 min, no meetings, no human)
 Gate: all APIs respond, URL parsing works, containers clean up
═══════════════════════════════════════════════════════════════

parallel:
    branch urls:
        3. urls
           call: src/urls(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN})
           => TEAMS_URLS_OK

    branch dashboard_t1:
        4. dashboard_backend
           call: src/dashboard — tier 1 only (steps 1-4)
           => DASHBOARD_T1_OK

    branch ws:
        5. websocket_protocol
           call: src/websocket — tier 1 only (steps 1-4)
           => WS_PROTOCOL_OK

    branch hooks:
        6. webhooks
           call: src/webhooks(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, DEPLOY_MODE={DEPLOY_MODE})
           => WEBHOOK_OK

    branch lifecycle:
        7. containers
           call: src/containers(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, DEPLOY_MODE={DEPLOY_MODE})
           => LIFECYCLE_OK

    join: wait for all

=> TIER_REACHED = 1
emit FINDING "tier 1: smoke — urls={TEAMS_URLS_OK} dashboard={DASHBOARD_T1_OK} ws={WS_PROTOCOL_OK} webhooks={WEBHOOK_OK} containers={LIFECYCLE_OK}"
on_fail: continue to tier 2 (smoke failures don't block auth tests)

═══════════════════════════════════════════════════════════════
 TIER 2 — Auth + Data (2 min, no meetings, no human)
 Gate: login works, data flows through proxy, S3 roundtrip works
═══════════════════════════════════════════════════════════════

8. dashboard_auth
   call: src/dashboard — tier 2 (steps 5-8: login, meetings, field contract, transcript proxy)
   => DASHBOARD_T2_OK, COOKIE_TOKEN
   on_fail: continue

9. browser_t1
   call: src/browser — tier 1 only (steps 1-16: create, CDP, S3 roundtrip)
   => SESSION_TOKEN, CDP_URL, SAVED_STATE, ROUNDTRIP_OK
   on_fail: continue

=> TIER_REACHED = 2
emit FINDING "tier 2: auth — dashboard={DASHBOARD_T2_OK} browser_roundtrip={ROUNDTRIP_OK}"

═══════════════════════════════════════════════════════════════
 TIER 3 — Meetings (15 min, needs browser session, human for login)
 Gate: bot joins, gets admitted, transcribes, finalizes
═══════════════════════════════════════════════════════════════

10. browser_t2
    > Ensure Google login works for meeting creation.
    call: src/browser — tier 2 (steps 17-21: check/do Google login, verify persistence)
    => LOGIN_PERSISTED
    if not LOGIN_PERSISTED:
        emit FAIL "Google login not available — cannot create GMeet meetings"
        > Skip GMeet branch, continue with Teams if URL available
    on_fail: continue

11. gmeet_chain
    if LOGIN_PERSISTED:
        11a. create_meeting
             call: src/meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet")
             => GMEET_URL, GMEET_NATIVE_ID

        11b. launch_bots
             call: src/bot(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_URL={GMEET_URL}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={GMEET_NATIVE_ID})
             => GMEET_RECORDER_ID

        11c. admit
             call: src/admit(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, SESSION_TOKEN={SESSION_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={GMEET_NATIVE_ID})

        11d. transcription
             call: src/transcription(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={GMEET_NATIVE_ID})
             => GMEET_SEGMENTS, GMEET_WER, GMEET_SPEAKER_ACC

        11e. finalize
             call: src/finalize(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={GMEET_NATIVE_ID})
             => GMEET_FINALIZED

        11f. post_meeting
             call: src/post-meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={GMEET_NATIVE_ID})
             => GMEET_POST_SEGMENTS

    on_fail: continue

12. teams_chain
    > Teams meeting URL: either from human, from host-teams-meeting-auto skill, or from env.
    > The proc asks if not available.
    call: src/meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="teams")
    => TEAMS_URL, TEAMS_NATIVE_ID

    if TEAMS_NATIVE_ID:
        12a-f. (same chain as gmeet: bot → admit → transcription → finalize → post-meeting)
        => TEAMS_SEGMENTS, TEAMS_WER, TEAMS_SPEAKER_ACC, TEAMS_FINALIZED, TEAMS_POST_SEGMENTS
    on_fail: continue

=> TIER_REACHED = 3
emit FINDING "tier 3: meetings — gmeet_segs={GMEET_SEGMENTS} teams_segs={TEAMS_SEGMENTS}"

═══════════════════════════════════════════════════════════════
 TIER 4 — Render (2 min, headless browser)
 Gate: dashboard page renders transcript and correct status
═══════════════════════════════════════════════════════════════

13. dashboard_render
    call: src/dashboard — tier 3 (steps 9-11: headless browser page load, transcript in DOM, status match, cache headers)
    => DASHBOARD_T3_OK
    on_fail: continue

=> TIER_REACHED = 4

═══════════════════════════════════════════════════════════════
 SCORE
═══════════════════════════════════════════════════════════════

14. score
    > Compute from feature DoD tables, not hardcoded weights.
    > Each feature README has a DoD table with weights.
    > The score is: sum(weight) for all PASS items / sum(weight) for all items.

    CHECKS = [
        > Ceiling items — any FAIL = instant zero
        {desc: "GMeet bot joins + transcribes",       weight: 10, ceiling: true,  pass: GMEET_SEGMENTS > 0},
        {desc: "Teams bot joins + transcribes",       weight: 10, ceiling: true,  pass: TEAMS_SEGMENTS > 0},
        {desc: "Dashboard credentials valid",          weight: 10, ceiling: true,  pass: DASHBOARD_T1_OK},
        {desc: "Login works",                          weight: 10, ceiling: true,  pass: DASHBOARD_T2_OK},
        {desc: "API field contract",                   weight: 10, ceiling: true,  pass: DASHBOARD_T2_OK},

        > Scored items
        {desc: "Teams 2+ speakers attributed",         weight: 8,  ceiling: false, pass: TEAMS_SPEAKER_ACC >= 0.5},
        {desc: "GMeet post-meeting transcript",        weight: 6,  ceiling: false, pass: GMEET_POST_SEGMENTS > 0},
        {desc: "Teams post-meeting transcript",        weight: 6,  ceiling: false, pass: TEAMS_POST_SEGMENTS > 0},
        {desc: "Browser S3 roundtrip",                 weight: 6,  ceiling: false, pass: ROUNDTRIP_OK},
        {desc: "Browser login persists",               weight: 6,  ceiling: false, pass: LOGIN_PERSISTED},
        {desc: "Dashboard transcript renders (browser)",weight: 5,  ceiling: false, pass: DASHBOARD_T3_OK},
        {desc: "Teams URL formats parsed",             weight: 5,  ceiling: false, pass: TEAMS_URLS_OK},
        {desc: "WS protocol",                          weight: 5,  ceiling: false, pass: WS_PROTOCOL_OK},
        {desc: "Webhooks fire + no secret leak",       weight: 5,  ceiling: false, pass: WEBHOOK_OK},
        {desc: "No orphan containers",                 weight: 5,  ceiling: false, pass: LIFECYCLE_OK},
        {desc: "Meeting chat",                         weight: 5,  ceiling: false, pass: CHAT_OK}
    ]

    for CHECK in CHECKS where ceiling == true:
        if CHECK has data and not CHECK.pass:
            => CONFIDENCE = 0
            emit FAIL "ceiling failed: {CHECK.desc}"
            stop

    => CONFIDENCE = sum(CHECK.weight for CHECK in CHECKS if CHECK.pass)
    emit FINDING "confidence: {CONFIDENCE}/100 (tier reached: {TIER_REACHED}/4)"

15. cleanup
    if TARGET_MODE == "lite" and VM_ID:
        ask: "Destroy VM {VM_IP} (id={VM_ID})? Logs saved to {LOG_FILE}. (yes/keep)"
        if response == "yes":
            call: vm.destroy(VM_ID={VM_ID})
            emit PASS "VM destroyed"
        else:
            emit FINDING "VM kept alive at {VM_IP} for debugging"

16. finish
    call: log.summary(MODULE="validate", TIER_REACHED={TIER_REACHED}, CONFIDENCE={CONFIDENCE}, DEPLOY_MODE={DEPLOY_MODE})
    call: log.close()
```

## Compose vs Lite differences

The procs handle these internally via `DEPLOY_MODE`:

| Aspect | Compose | Lite |
|--------|---------|------|
| Container names | `vexa-dashboard-1` etc | `vexa` (single container) |
| Port mapping | Separate per service | All on one host |
| Inter-container | Docker network DNS | localhost |
| Health checks | Per-container | supervisord processes |
| `docker exec` target | Service-specific container | `vexa` for everything |

No cookbook-level branching needed. `src/infra` step 1 detects mode, all downstream procs use it.

## When to run each tier

| Scenario | Tiers | Time | Human |
|----------|-------|------|-------|
| After code change | 0-1 | 5 min | no |
| After deploy/restart | 0-2 | 7 min | no |
| Before release | 0-4 | 25 min | login once |
| CI/CD gate | 0-1 | 5 min | no |
| Debug "dashboard broken" | 0, 2, 4 | 5 min | no |
