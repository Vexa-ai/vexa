---
needs: []
gives: [CONFIDENCE]
---

use: env
use: lib/log

# Lifecycle Validate

> **Why:** Container, bot, and browser session lifecycles are at 50-81%. This proc brings all three to 90+ on any deployment (compose, lite, helm).
> **What:** Provision throwaway infra, deploy, run atomic lifecycle tests, score against DoDs.
> **How:** Fresh VM (compose/lite) or fresh namespace (helm). Atomic primitives run in optimal order — parallel where independent, sequential where dependent. Each primitive is timed. Total wall-clock target: 25 min.

## Primitives

Each primitive tests exactly one thing. Reusable across deployments.

| ID | Primitive | Time | Needs | Gives | DoD |
|----|-----------|------|-------|-------|-----|
| P1 | `create_bot(platform, meeting_id)` | 3s | GATEWAY, TOKEN | BOT_ID | bot#1 |
| P2 | `wait_status(bot, target, timeout)` | 5-120s | GATEWAY, TOKEN | STATUS | bot#2 |
| P3 | `stop_bot(platform, meeting_id)` | 5s | GATEWAY, TOKEN | — | bot#3 |
| P4 | `verify_removed(container)` | 15s | DEPLOY_MODE | — | container#2,#3 |
| P5 | `verify_transition(meeting_id, from, to)` | 2s | GATEWAY, TOKEN | — | bot#14 |
| P6 | `create_browser_session()` | 15s | GATEWAY, TOKEN | SESSION_TOKEN | browser#1 |
| P7 | `touch_and_verify(container)` | 5s | RUNTIME_URL | UPDATED_AT | container#6, browser#15 |
| P8 | `verify_idle_timeout(container, profile)` | 2s | RUNTIME_URL | — | container#5, browser#16 |
| P9 | `verify_profile(container, expected)` | 2s | RUNTIME_URL | — | browser#16 |
| P10 | `login_dashboard(email)` | 3s | DASHBOARD_URL | COOKIE | dashboard#3 |
| P11 | `verify_transcript_renders(meeting_id)` | 10s | DASHBOARD_URL, COOKIE | — | dashboard#7 |
| P12 | `create_meeting_gmeet()` | 20s | SESSION_TOKEN | MEETING_URL | bot#2 |
| P13 | `auto_admit(session, meeting)` | 15s | SESSION_TOKEN | — | bot#8 |
| P14 | `send_tts(meeting, token, text)` | 10s | GATEWAY, TOKEN | — | — |
| P15 | `fetch_transcript(platform, meeting)` | 2s | GATEWAY, TOKEN | SEGMENTS | — |
| P16 | `verify_concurrency_release(meeting)` | 10s | GATEWAY, TOKEN | — | bot#13 |
| P17 | `verify_callback_delivered(meeting)` | 2s | GATEWAY, TOKEN | — | container#7 |
| P18 | `verify_cdp_proxy(session_token)` | 5s | GATEWAY | — | browser#2 |
| P19 | `verify_s3_roundtrip(session_token)` | 30s | GATEWAY, TOKEN | — | browser#7 |
| P20 | `verify_creation_transition(meeting)` | 2s | GATEWAY, TOKEN | — | browser#17 |
| P21 | `verify_resource_limits(container, profile)` | 5s | DEPLOY_MODE | — | container#4 |
| P22 | `verify_reconciliation()` | 30s | RUNTIME_URL | — | container#8 |
| P23 | `verify_escalation(meeting)` | 60s | GATEWAY, TOKEN | — | bot#11 |
| P24 | `verify_auto_save(session, bucket)` | 75s | GATEWAY, MINIO | — | browser#6 |
| P25 | `verify_auth_flag(token)` | 10s | GATEWAY, TOKEN | — | browser#9 |
| P26 | `verify_shutdown_save(session, bucket)` | 15s | GATEWAY, TOKEN, MINIO | — | browser#12 |
| P27 | `verify_k8s_resources(namespace, profile)` | 5s | K8S_NAMESPACE | — | container#15 |

## state

    DEPLOY_TARGET = ""     # compose | lite | helm
    BRANCH        = ""
    VM_IP         = ""
    VM_ID         = ""
    K8S_NAMESPACE = ""
    CONFIDENCE    = {container: 0, bot: 0, browser: 0, dashboard: 0}

## steps

```
0. init
   call: log.init(COOKBOOK="lifecycle-validate")
   if DEPLOY_TARGET not set: ask "compose / lite / helm?"
   if BRANCH not set: do: git rev-parse --abbrev-ref HEAD => BRANCH

═══════════════════════════════════════════════════════════════
 PROVISION (~5 min)
═══════════════════════════════════════════════════════════════

1. provision
   call: src/provision(DEPLOY_TARGET={DEPLOY_TARGET}, BRANCH={BRANCH})
   => VM_IP, VM_ID, SSH, K8S_NAMESPACE, GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL, USER_ID, API_TOKEN
   on_fail: stop

═══════════════════════════════════════════════════════════════
 LIFECYCLE TESTS (~15 min)
 Optimal graph: parallel where independent, sequential where dependent.
═══════════════════════════════════════════════════════════════

> Phase A: Independent tests — run all in parallel (~30s)
> No meeting needed. No browser session needed.

parallel:                                                         # ~75s wall
    branch container_basics:
        2a. create_and_remove                                     # 25s
            P1: create_bot("google_meet", "lifecycle-test-1")     # 3s
            P2: wait_status(active or awaiting_admission, 30s)    # 10s
            P21: verify_resource_limits(container, "meeting")     # 5s
            P3: stop_bot                                          # 5s
            wait 5s
            P4: verify_removed(container)                         # 2s
            P5: verify_transition(meeting, requested→...→completed) # 2s
            P17: verify_callback_delivered(meeting)               # 2s
            => container#1,#2,#3,#4,#7 + bot#1,#3,#12,#14

    branch concurrency:
        2b. concurrency_release                                   # 15s
            P16: create A → stop A → create B immediately
            => bot#13

    branch dashboard_auth:
        2c. login_and_field_contract                              # 10s
            P10: login_dashboard
            verify: GET /meetings/{id} returns native_meeting_id
            => dashboard#3,#4,#5

    branch timeout:
        2d. bot_timeout                                           # 60s
            P1: create_bot with no_one_joined_timeout=30000
            wait 60s
            verify: status in [completed, failed]
            => bot#5

    branch escalation:
        2e. needs_human_help                                      # 60s
            P23: create bot pointing at invalid/expired meeting
            wait 60s
            verify: status == needs_human_help, meeting.data.escalation exists
            => bot#11

    branch k8s_resources [only if DEPLOY_MODE == helm]:
        2f. k8s_propagation                                       # 5s
            P27: verify_k8s_resources(namespace, "meeting")
            verify: pod has correct requests/limits from profile
            => container#15

    join: wait for all

> Phase B: Browser session — sequential, needs Phase A done (~60s)

3. browser_session_lifecycle                                      # 90s
    P6: create_browser_session()                                  # 15s
    P9: verify_profile(container, "browser-session")              # 2s
    P21: verify_resource_limits(container, "browser-session")     # 5s
    P20: verify_creation_transition(meeting)                      # 2s
    P18: verify_cdp_proxy(session_token)                          # 5s
    P8: verify_idle_timeout(container, 3600)                      # 2s
    P7: touch_and_verify(container)                               # 5s
    P25: verify_auth_flag(token)                                  # 10s
    P19: verify_s3_roundtrip(session_token)                       # 30s
    P24: verify_auto_save(session, bucket)                        # 75s
    => browser#1,#2,#3,#4,#5,#6,#7,#8,#9,#15,#16,#17 + container#4,#5,#6,#10

4. browser_stop                                                   # 20s
    P26: verify_shutdown_save(session, bucket)                    # 15s
        > check MinIO timestamp, then stop, check timestamp updated
    P3: stop browser session                                      # 5s
    wait 10s
    P4: verify_removed                                            # 2s
    P5: verify_transition(active→completed)                       # 2s
    => browser#12,#18 + container#2

> Phase B2: Reconciliation — restart runtime-api, verify state syncs (~30s)

4b. reconciliation                                                # 30s
    P22: restart runtime-api → verify running containers still in Redis
    > Create a bot, note it in Redis, restart runtime-api, verify container still tracked
    => container#8

> Phase C: Meeting chain — needs browser session for GMeet (~10 min)
> Only if browser has Google login saved.

5. check_google_login                                             # 15s
    P6: create_browser_session (with saved userdata)
    check: myaccount.google.com reachable (not login page)
    if LOGIN_REQUIRED:
        ask: "Log into Google, type 'done'" [human]
        save to S3

6. gmeet_chain                                                    # 5 min
    P12: create_meeting_gmeet                                     # 20s
    P1: create recorder bot                                       # 3s
    P1: create speaker bot A (separate user)                      # 3s
    P1: create speaker bot B (separate user)                      # 3s
    P2: wait all in lobby                                         # 30s
    P13: auto_admit (repeat for each bot)                         # 45s
    P2: wait all active                                           # 10s
    P14: send_tts alternating A and B (6 utterances)              # 60s
    wait 30s
    P15: fetch_transcript → verify 2+ speakers                   # 2s
    P3: stop all bots                                             # 5s
    P4: verify all removed                                        # 15s
    P5: verify transitions for each                               # 5s
    => bot#2,#6,#7,#8 + container#2,#3

7. teams_chain                                                    # 3 min
    ask: "Teams meeting URL?" or use host-teams-meeting-auto
    P1: create recorder + speaker A + speaker B                   # 10s
    P2: wait all active (Teams auto-admits)                       # 30s
    P14: send_tts alternating                                     # 60s
    wait 30s
    P15: fetch_transcript → verify 2+ speakers                   # 2s
    P3: stop all                                                  # 5s
    P4: verify removed                                            # 15s
    => bot#2,#6

> Phase D: Dashboard render — needs completed meeting with transcript (~30s)

8. dashboard_render                                               # 30s
    P10: login_dashboard                                          # 3s
    P11: verify_transcript_renders (completed gmeet meeting)      # 10s
    verify: page status matches API status                        # 10s
    => dashboard#7,#8

═══════════════════════════════════════════════════════════════
 SCORE
═══════════════════════════════════════════════════════════════

9. score
    for FEATURE in [container-lifecycle, bot-lifecycle, browser-session, dashboard]:
        read feature DoD table
        PASS_WEIGHT = sum(weight for PASS items)
        TOTAL_WEIGHT = sum(all weights)
        CONFIDENCE[FEATURE] = PASS_WEIGHT * 100 / TOTAL_WEIGHT
        emit FINDING "{FEATURE}: {CONFIDENCE}%"

    LIFECYCLE_CONFIDENCE = min(CONFIDENCE.values())
    if LIFECYCLE_CONFIDENCE >= 90:
        emit PASS "all lifecycle features 90+%"
    else:
        emit FAIL "lowest: {min} at {confidence}%"

═══════════════════════════════════════════════════════════════
 CLEANUP
═══════════════════════════════════════════════════════════════

10. cleanup
    stop any running bots/sessions
    if VM_ID:
        ask: "Destroy VM? (yes / keep)"
        if yes: call: vm.destroy(VM_ID)
    if K8S_NAMESPACE:
        ask: "Delete namespace? (yes / keep)"
        if yes: do: helm uninstall vexa -n {K8S_NAMESPACE} && kubectl delete namespace {K8S_NAMESPACE}
    call: log.close()
```

## Time budget

| Phase | Parallel? | Wall time |
|-------|-----------|-----------|
| Provision | no | 5 min |
| A: independent tests | yes (4 branches) | 1 min (longest: timeout 60s) |
| B: browser lifecycle | no | 1.5 min |
| C: meeting chain (GMeet + Teams) | no | 8 min |
| D: dashboard render | no | 0.5 min |
| Score + cleanup | no | 0.5 min |
| **Total** | | **~17 min** |

Without meetings (C): **8 min**. Without provision: **12 min**.

## Deployment differences

Primitives handle this internally via DEPLOY_MODE:

| Primitive | compose | lite | helm |
|-----------|---------|------|------|
| verify_removed | `docker ps -a --filter name=` | `docker exec vexa ps` | `kubectl get pod -n` |
| verify_profile | `GET /containers/{name}` | same | same |
| touch_and_verify | `POST /containers/{name}/touch` | same | same |
| create_meeting_gmeet | CDP via gateway proxy | same | same |
| auto_admit | CDP via gateway proxy | same | same |

Gateway URL, runtime URL, dashboard URL differ per deployment — resolved in provision step by `src/infra`.
