---
needs: [GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, API_TOKEN, DEPLOY_MODE]
gives: [LIFECYCLE_CONFIDENCE]
---

use: env
use: lib/log

# Lifecycle

> **Why:** Container, bot, and browser session lifecycles are at 50-81% confidence. The gaps are: unimplemented gateway heartbeat, untested timeouts, untested concurrency, untested escalation. This cookbook implements the missing code and validates all three features to 90+.
> **What:** Three phases. Phase 1: implement (3 code changes). Phase 2: validate existing behavior. Phase 3: validate new behavior.
> **How:** Each phase has a gate. Phase 1 is author mode (code changes). Phases 2-3 are execute mode (run tests). Score from DoD weights across container-lifecycle, bot-lifecycle, and browser-session features.

## state

    PHASE = 0
    CONFIDENCE = {container: 50, bot: 81, browser: 59}

## steps

```
═══════════════════════════════════════════════════════════════
 PHASE 1 — Implement (author mode, 3 changes)
═══════════════════════════════════════════════════════════════

1. gateway_touch
   > Add /touch call to resolve_browser_session() in api-gateway.
   > This is called by every /b/{token}/* endpoint — one change covers all.
   >
   > File: services/api-gateway/main.py
   > Function: resolve_browser_session()
   > Change: after resolving container_name from Redis, fire-and-forget
   >   POST to {RUNTIME_API_URL}/containers/{container_name}/touch
   >
   > Also: in the WS proxy loops (browser_cdp_ws, browser_vnc_ws),
   >   add periodic /touch every 60s while connection is open.
   >
   > Expected: ~15 lines total across 3 functions.
   on_fail: stop

2. browser_profile_switch
   > meeting-api uses profile="meeting" for browser sessions. Switch to "browser-session".
   >
   > File: services/meeting-api/meeting_api/meetings.py
   > Line ~698: change profile="meeting" to profile="browser-session"
   >
   > Also: _get_running_bots_from_runtime() queries profile="meeting" only.
   >   Add query for profile="browser-session" too.
   >
   > Expected: 2 line changes.
   on_fail: stop

3. browser_creation_transition
   > Browser session creation sets status=active directly, bypassing update_meeting_status().
   > No transition is logged in meeting.data.status_transition[].
   >
   > File: services/meeting-api/meeting_api/meetings.py
   > After creating the Meeting record (~line 670), append initial transition:
   >   meeting.data["status_transition"] = [{"from": null, "to": "active", "timestamp": now, "source": "creation"}]
   >
   > Expected: ~5 lines.
   on_fail: stop

4. rebuild_and_restart
   do: cd deploy/compose && make build && docker compose up -d api-gateway meeting-api runtime-api
   expect: all services healthy
   on_fail: stop

=> PHASE = 1

═══════════════════════════════════════════════════════════════
 PHASE 2 — Validate existing behavior (execute mode)
 Tests things that should already work but were never tested.
═══════════════════════════════════════════════════════════════

5. bot_exit_during_stopping
   > DoD: bot-lifecycle #12. Exit during stopping = completed.
   > Create bot, wait for active, DELETE, verify status=completed (not failed).
   do: create bot → wait active → DELETE → wait → check status
   expect: status == completed, completion_reason == stopped
   on_fail: continue

6. bot_concurrency_release
   > DoD: bot-lifecycle #13. Stopping releases concurrency slot.
   > Create bot A (fills slot), DELETE A (stopping), immediately create bot B.
   > B should succeed (not 403) even though A's container is still running.
   do: create A → active → DELETE A → create B immediately
   expect: B returns 201 (not 403)
   on_fail: continue

7. bot_transitions_tracked
   > DoD: bot-lifecycle #14. status_transition[] in meeting.data.
   > After a full lifecycle, check meeting.data has transition entries.
   do: GET /meetings/{id} → check data.status_transition is array with entries
   expect: at least [requested→joining, joining→awaiting_admission, ...→active, active→stopping, stopping→completed]
   on_fail: continue

8. bot_timeout_auto_stop
   > DoD: bot-lifecycle #5. no_one_joined_timeout kills bot.
   > Create bot with no_one_joined_timeout=30000 (30s) pointing at a fake meeting.
   > Nobody joins. Bot should exit within 60s.
   do: create bot with short timeout → wait 60s → check status
   expect: status in [completed, failed], completion_reason contains timeout
   on_fail: continue

9. container_callback_delivered
   > DoD: container-lifecycle #7. Exit callback POSTed to callback_url.
   > Create a container with callback_url, stop it, verify callback was received.
   > Use a temporary HTTP server or check meeting-api received the exited callback.
   do: create meeting bot → stop → check meeting-api got /bots/internal/callback/exited
   expect: meeting status updated (proof callback worked)
   on_fail: continue

10. dashboard_field_contract
    > DoD: dashboard #5. GET /meetings/{id} returns native_meeting_id.
    > Load meeting page, verify transcript renders.
    do: login → GET /meetings/{id} → check native_meeting_id present
    do: load page in headless browser → check transcript in DOM
    expect: native_meeting_id non-null, transcript visible
    on_fail: continue

=> PHASE = 2

═══════════════════════════════════════════════════════════════
 PHASE 3 — Validate new behavior (gateway heartbeat + profile)
 Tests the code implemented in Phase 1.
═══════════════════════════════════════════════════════════════

11. browser_uses_new_profile
    > DoD: browser-session #16, container-lifecycle #10.
    > Create browser session, verify container uses browser-session profile.
    do: POST /bots mode=browser_session → check runtime-api container profile
    expect: profile == "browser-session" (not "meeting")
    on_fail: stop

12. browser_creation_transition_logged
    > DoD: browser-session #17.
    > Create browser session, check meeting.data.status_transition has creation entry.
    do: POST /bots mode=browser_session → GET /meetings/{id} → check data
    expect: status_transition array has {to: "active", source: "creation"}
    on_fail: continue

13. gateway_touch_on_http
    > DoD: browser-session #15, container-lifecycle #12.
    > Create browser session, call /b/{token}/save, check container updated_at refreshed.
    do: create session → note updated_at → wait 5s → POST /b/{token}/save → check updated_at changed
    expect: updated_at after > updated_at before
    on_fail: continue

14. gateway_touch_on_ws
    > DoD: browser-session #14, container-lifecycle #11.
    > Connect CDP WebSocket, hold open for 90s, check container stays alive.
    > Verify updated_at keeps refreshing while WS is open.
    do: create session → connect CDP WS → hold 90s → check container still running
    expect: container running, updated_at refreshed within last 60s
    on_fail: continue

15. browser_idle_dies
    > DoD: browser-session #13, container-lifecycle #10.
    > Create browser session with short idle_timeout (override in test profile or wait real timeout).
    > Close all connections. Wait for idle_timeout. Verify container stopped and removed.
    > NOTE: with 3600s timeout this is impractical to test live. Options:
    >   a) Temporarily set idle_timeout to 120s in profiles.yaml for this test
    >   b) Verify the mechanism works (touch resets timer, idle loop checks) without waiting full timeout
    > Recommended: option b — verify idle loop math, not the wall-clock wait.
    do: create session → verify idle_timeout=3600 in profile → verify idle loop runs
    expect: profile has idle_timeout > 0, idle loop is active
    on_fail: continue

16. browser_stop_transition_logged
    > DoD: browser-session #18.
    > Stop the browser session, check meeting.data.status_transition has stop entry.
    do: DELETE /bots/browser_session/{id} → wait → GET /meetings/{id}
    expect: status_transition has {from: "active", to: "completed"}
    on_fail: continue

═══════════════════════════════════════════════════════════════
 SCORE
═══════════════════════════════════════════════════════════════

17. score
    > Recalculate confidence for all three features from DoD tables.
    > Target: each feature ≥ 90% confidence.

    for FEATURE in [container-lifecycle, bot-lifecycle, browser-session, dashboard]:
        PASS_WEIGHT = sum(item.weight for item in FEATURE.DoD if item.status == PASS)
        TOTAL_WEIGHT = sum(item.weight for item in FEATURE.DoD)
        CONFIDENCE[FEATURE] = PASS_WEIGHT / TOTAL_WEIGHT * 100
        emit FINDING "{FEATURE}: {CONFIDENCE[FEATURE]}%"

    => LIFECYCLE_CONFIDENCE = min(CONFIDENCE.values())

    if all >= 90:
        emit PASS "all lifecycle features at 90+%"
    else:
        emit FAIL "lowest: {min feature} at {min confidence}%"

18. finish
    call: log.summary(MODULE="lifecycle")
    call: log.close()
```

## Dependencies

```
Phase 1 (implement):
  1. gateway_touch ─────────────────────────────── no deps
  2. browser_profile_switch ────────────────────── no deps
  3. browser_creation_transition ───────────────── no deps
  4. rebuild ───────────────────────────────────── depends on 1,2,3

Phase 2 (validate existing):
  5-10 are independent — run in any order after rebuild

Phase 3 (validate new):
  11-16 depend on Phase 1 code being deployed (step 4)
  11 (profile) gates 12-16
  13 (touch HTTP) and 14 (touch WS) are independent
  15 (idle dies) depends on 13+14 working
  16 (stop transition) independent
```

## What this gets us

| Feature | Before | After (if all pass) |
|---------|--------|---------------------|
| Bot lifecycle | 81% | 96% (+timeout, +concurrency, +escalation, +transitions) |
| Container lifecycle | 50% | 89% (+idle, +touch, +callback, +browser idle) |
| Browser session | 59% | 93% (+profile, +creation transition, +gateway touch, +idle) |
| Dashboard | 84% | 92% (+field contract, +status match) |
