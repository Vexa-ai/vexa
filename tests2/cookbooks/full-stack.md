---
needs: []
gives: [CONFIDENCE]
---

use: env
use: lib/log

# Full Stack

> **Why:** Before a release, you need to know everything works end-to-end across both platforms.
> **What:** The complete test suite — deploy validation, all services, GMeet + Teams in parallel, RT quality, confidence scoring.
> **How:** Runs every src/ module in DAG order. Parallel branches for GMeet and Teams. Ends with a weighted score out of 100 (ceiling checks = instant zero).

## state

    CONFIDENCE = 0

## steps

```
1. init_log
   call: log.init(COOKBOOK="full-stack")
   => LOG_FILE, JSONL_FILE
   call: log.emit(EVENT="START", MODULE="full-stack", STEP="init", MSG="starting full stack validation")

2. deploy
   call: src/deploy
   => DEPLOY_METHODS, ALL_GAPS

3. infra
   call: src/infra
   => GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL

4. api
   call: src/api(GATEWAY_URL={GATEWAY_URL}, ADMIN_URL={ADMIN_URL}, ADMIN_TOKEN={ADMIN_TOKEN})
   => USER_ID, API_TOKEN

5. urls
   call: src/urls(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN})
   => TEAMS_URLS_OK

6. dashboard
   call: src/dashboard(GATEWAY_URL={GATEWAY_URL}, ADMIN_URL={ADMIN_URL}, ADMIN_TOKEN={ADMIN_TOKEN}, DASHBOARD_URL={DASHBOARD_URL}, DEPLOY_MODE={DEPLOY_MODE}, API_TOKEN={API_TOKEN})
   => DASHBOARD_OK

7. browser
   call: src/browser(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, DASHBOARD_URL={DASHBOARD_URL})
   => SESSION_TOKEN, CDP_URL, SAVED_STATE

parallel:
    branch gmeet:
        8a. meeting
            call: src/meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet")
            => GMEET_URL, GMEET_NATIVE_ID

        9a. bot
            call: src/bot(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_URL={GMEET_URL}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={GMEET_NATIVE_ID})
            => GMEET_RECORDER_ID

        10a. admit
            call: src/admit(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, SESSION_TOKEN={SESSION_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={GMEET_NATIVE_ID})

        11a. transcription
             call: src/transcription(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={GMEET_NATIVE_ID})
             => GMEET_SEGMENTS, GMEET_WER, GMEET_SPEAKER_ACC

        12a. post_meeting
             call: src/post-meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={GMEET_NATIVE_ID})

    branch teams:
        8b. meeting
            call: src/meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="teams")
            => TEAMS_URL, TEAMS_NATIVE_ID

        9b. bot
            call: src/bot(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_URL={TEAMS_URL}, MEETING_PLATFORM="teams", NATIVE_MEETING_ID={TEAMS_NATIVE_ID})
            => TEAMS_RECORDER_ID

        10b. admit
            call: src/admit(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, SESSION_TOKEN={SESSION_TOKEN}, MEETING_PLATFORM="teams", NATIVE_MEETING_ID={TEAMS_NATIVE_ID})

        11b. transcription
             call: src/transcription(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="teams", NATIVE_MEETING_ID={TEAMS_NATIVE_ID})
             => TEAMS_SEGMENTS, TEAMS_WER, TEAMS_SPEAKER_ACC

        12b. post_meeting
             call: src/post-meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="teams", NATIVE_MEETING_ID={TEAMS_NATIVE_ID})

    join: wait for both

13. finalize_gmeet
    call: src/finalize(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={GMEET_NATIVE_ID})

14. finalize_teams
    call: src/finalize(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="teams", NATIVE_MEETING_ID={TEAMS_NATIVE_ID})

parallel:
    branch ws:
        15. websocket
            call: src/websocket(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN})
            => WEBSOCKET_OK
    branch hooks:
        16. webhooks
            call: src/webhooks(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, DEPLOY_MODE={DEPLOY_MODE})
            => WEBHOOK_OK
    branch lifecycle:
        17. containers
            call: src/containers(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, DEPLOY_MODE={DEPLOY_MODE})
            => LIFECYCLE_OK, ORPHAN_COUNT
    join: wait for all

18. rt_quality
    call: src/rt-collect(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet")
    => DATASET_PATH
    call: src/rt-replay(DATASET_PATH={DATASET_PATH})
    => WER, SPEAKER_ACCURACY, COMPLETENESS
    call: src/rt-delivery(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, DATASET_PATH={DATASET_PATH})
    => DELIVERY_OK

19. score
    CHECKS = [
        {desc: "GMeet bot joins + transcribes",     weight: 10, ceiling: true,  pass: GMEET_SEGMENTS > 0},
        {desc: "Teams bot joins + transcribes",     weight: 10, ceiling: true,  pass: TEAMS_SEGMENTS > 0},
        {desc: "GMeet 3+ speakers attributed",      weight: 8,  ceiling: true,  pass: GMEET_SPEAKER_ACC >= 0.95},
        {desc: "Teams 3+ speakers attributed",      weight: 8,  ceiling: true,  pass: TEAMS_SPEAKER_ACC >= 0.95},
        {desc: "GMeet post-meeting transcription",  weight: 6,  ceiling: false, pass: GMEET_POST_SEGMENTS > 0},
        {desc: "Teams post-meeting transcription",  weight: 6,  ceiling: false, pass: TEAMS_POST_SEGMENTS > 0},
        {desc: "Browser session persists login",    weight: 6,  ceiling: false, pass: SAVED_STATE == true},
        {desc: "TTS speech heard by others",        weight: 6,  ceiling: false, pass: GMEET_SEGMENTS > 0},
        {desc: "Dashboard shows transcript",        weight: 5,  ceiling: false, pass: DASHBOARD_OK},
        {desc: "Teams URL formats parsed",          weight: 5,  ceiling: false, pass: TEAMS_URLS_OK},
        {desc: "Auth: invalid token rejected",      weight: 5,  ceiling: false, pass: API_TOKEN exists},
        {desc: "WS delivery matches REST",          weight: 5,  ceiling: false, pass: WEBSOCKET_OK},
        {desc: "Webhooks fire",                     weight: 5,  ceiling: false, pass: WEBHOOK_OK},
        {desc: "No orphan containers",              weight: 5,  ceiling: false, pass: LIFECYCLE_OK},
        {desc: "Bot lifecycle complete",             weight: 5,  ceiling: false, pass: FINALIZATION_OK},
        {desc: "Meeting chat read/write",           weight: 5,  ceiling: false, pass: CHAT_OK}
    ]

    for CHECK in CHECKS where ceiling == true:
        if not CHECK.pass:
            => CONFIDENCE = 0
            emit FAIL "ceiling failed: {CHECK.desc}"
            stop

    => CONFIDENCE = sum(CHECK.weight for CHECK in CHECKS if CHECK.pass)
    emit FINDING "confidence: {CONFIDENCE}/{CONFIDENCE_TARGET}"

    for CHECK in CHECKS:
        emit FINDING "  {'PASS' if CHECK.pass else 'FAIL'} [{CHECK.weight}] {CHECK.desc}"

20. finish
    call: log.summary(MODULE="full-stack", TOTAL_STEPS={step_count}, PASSED={pass_count}, FAILED={fail_count}, FIXED={fixed_count}, SKIPPED={skip_count})
    call: log.close()
    call: log.emit(EVENT="FINDING", MODULE="full-stack", STEP="finish", MSG="logs at {LOG_FILE}")
```
