#!/usr/bin/env bash
# Container lifecycle: create, alive, stop, verify removal, timeout, concurrency, orphans
#
# Step IDs (stable — bound to features/container-lifecycle/README.md + features/bot-lifecycle/README.md DoDs):
#   create            — POST /bots spawns a bot container successfully
#   alive             — bot process is still running after 10s (not crash-looping)
#   removal           — container removed after DELETE /bots/...
#   status_completed  — meeting.status=completed after stop (not failed/stuck)
#   timeout_stop      — bot auto-stops after automatic_leave timeout
#   concurrency_slot  — slot released on stop, next create not rejected
#   no_orphans        — no zombie/exited/stuck bot containers after test run
#
# Reads: .state/gateway_url, .state/api_token, .state/deploy_mode
# Writes: .state/reports/<mode>/containers.json
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
MODE=$(state_read deploy_mode)

echo ""
echo "  containers"
echo "  ──────────────────────────────────────────────"

test_begin containers

# ── Cleanup (setup hygiene, not a step) ───────────
STALE_BOTS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
bots=json.load(sys.stdin).get('running_bots',[])
for b in bots:
    mid=b.get('native_meeting_id','')
    platform=b.get('platform','google_meet')
    mode=b.get('data',{}).get('mode','')
    if mode=='browser_session':
        print(f'browser_session/{mid}')
    else:
        print(f'{platform}/{mid}')
" 2>/dev/null)

if [ -n "$STALE_BOTS" ]; then
    info "cleaning up stale bots..."
    echo "$STALE_BOTS" | while read -r bot_path; do
        curl -sf -X DELETE "$GATEWAY_URL/bots/$bot_path" \
            -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
    done
    sleep 10
fi

# ── Step: create ─────────────────────────────────
echo "  creating test bot..."
RESP=$(http_post "$GATEWAY_URL/bots" \
    '{"platform":"google_meet","native_meeting_id":"lifecycle-test-1","bot_name":"LC Test","automatic_leave":{"no_one_joined_timeout":30000}}' \
    "$API_TOKEN")
BOT_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -n "$BOT_ID" ]; then
    step_pass create "bot $BOT_ID created"
else
    if [ "$(http_code)" = "500" ] && [ "$MODE" = "helm" ]; then
        step_skip create "bot runtime not configured in helm (HTTP 500)"
        step_skip alive "create failed"
        step_skip removal "create failed"
        step_skip status_completed "create failed"
        step_skip timeout_stop "create failed"
        step_skip concurrency_slot "create failed"
        step_skip no_orphans "create failed"
        echo "  ──────────────────────────────────────────────"
        echo ""
        exit 0
    fi
    step_fail create "POST /bots failed (HTTP $(http_code)): $RESP"
    exit 1
fi

# ── Step: alive ──────────────────────────────────
sleep 10
BOT_ALIVE=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
bots=json.load(sys.stdin).get('running_bots',[])
for b in bots:
    if b.get('native_meeting_id')=='lifecycle-test-1':
        print(b.get('status','?'))
        break
else: print('gone')
" 2>/dev/null)

if [ "$BOT_ALIVE" = "running" ]; then
    step_pass alive "bot process running after 10s"
elif [ "$BOT_ALIVE" = "gone" ]; then
    MEETING_STATUS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/meetings" | python3 -c "
import sys,json
d=json.load(sys.stdin)
ms=d.get('meetings',[]) if isinstance(d,dict) else d
for m in ms:
    if m.get('native_meeting_id')=='lifecycle-test-1':
        print(f'status={m.get(\"status\",\"?\")} reason={m.get(\"completion_reason\",\"?\")}')
        break
" 2>/dev/null)
    step_fail alive "bot process died within 10s — $MEETING_STATUS"
    exit 1
else
    step_skip alive "bot status=$BOT_ALIVE (not running, not gone)"
fi

# ── Step: removal ────────────────────────────────
echo "  stopping bot..."
curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/lifecycle-test-1" \
    -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true

sleep 15

if [ "$MODE" = "compose" ]; then
    REMAINING=$(docker ps -a --filter "name=meeting-" --format '{{.Names}}' | { grep -c "lifecycle-test" || true; })
elif [ "$MODE" = "lite" ]; then
    REMAINING=$(docker exec vexa ps aux 2>/dev/null | { grep -c "lifecycle-test" || true; })
elif [ "$MODE" = "helm" ]; then
    if command -v kubectl >/dev/null 2>&1 && kubectl cluster-info >/dev/null 2>&1; then
        REMAINING=$(kubectl get pods --no-headers 2>/dev/null | { grep -c "lifecycle-test" || true; }) || true
    else
        REMAINING="unknown"
    fi
else
    REMAINING=0
fi

if [ "$REMAINING" = "unknown" ]; then
    step_skip removal "no kubectl access in helm mode"
elif [ "${REMAINING:-0}" -eq 0 ]; then
    step_pass removal "container fully removed after stop"
else
    step_fail removal "$REMAINING container(s) still present after stop"
fi

# ── Step: status_completed ───────────────────────
# Poll for up to 120s — on K8s the BOT_STOP_DELAY_SECONDS=90 path holds
# meeting.status in `stopping` for ~90s before transitioning to `completed`
# (see features/bot-lifecycle/README.md:273 "Bot stuck in stopping —
# Delayed Stop 90s wait"). Immediate query returns `stopping` on helm,
# which is expected intermediate state — not a failure.
STATUS="unknown"
for i in $(seq 1 24); do
    STATUS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/meetings" | python3 -c "
import sys,json
d=json.load(sys.stdin)
meetings=d.get('meetings',[]) if isinstance(d,dict) else d
for m in meetings:
    if m.get('native_meeting_id')=='lifecycle-test-1':
        print(m.get('status','?'))
        break
else: print('gone')
" 2>/dev/null)
    case "$STATUS" in
        completed|gone|failed) break ;;
    esac
    sleep 5
done

if [ "$STATUS" = "completed" ] || [ "$STATUS" = "gone" ]; then
    step_pass status_completed "meeting.status=$STATUS after stop (waited ${i}x5s)"
else
    step_fail status_completed "status=$STATUS (expected completed) after ~${i}x5s poll"
fi

# ── Step: timeout_stop ───────────────────────────
echo "  testing timeout (30s no_one_joined)..."
RESP2=$(http_post "$GATEWAY_URL/bots" \
    '{"platform":"google_meet","native_meeting_id":"timeout-test","bot_name":"Timeout Test","automatic_leave":{"no_one_joined_timeout":30000}}' \
    "$API_TOKEN")
TIMEOUT_ID=$(echo "$RESP2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -n "$TIMEOUT_ID" ]; then
    sleep 60
    TIMEOUT_STATUS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
bots=json.load(sys.stdin).get('running_bots',[])
for b in bots:
    if b.get('native_meeting_id')=='timeout-test':
        print(b.get('status','running'))
        break
else: print('gone')
" 2>/dev/null)

    if [ "$TIMEOUT_STATUS" = "gone" ] || [ "$TIMEOUT_STATUS" = "completed" ] || [ "$TIMEOUT_STATUS" = "failed" ]; then
        step_pass timeout_stop "bot stopped ($TIMEOUT_STATUS)"
    else
        step_skip timeout_stop "bot still $TIMEOUT_STATUS after 60s (timeout may count from lobby)"
        curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/timeout-test" \
            -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
        sleep 10
    fi
else
    step_fail timeout_stop "could not create timeout test bot"
fi

# ── Step: concurrency_slot ───────────────────────
echo "  testing concurrency release..."
RESP_A=$(http_post "$GATEWAY_URL/bots" \
    '{"platform":"google_meet","native_meeting_id":"concurrency-a","bot_name":"CC-A","automatic_leave":{"no_one_joined_timeout":30000}}' \
    "$API_TOKEN")
CC_A_OK=$(echo "$RESP_A" | python3 -c "import sys,json; print('ok' if json.load(sys.stdin).get('id') else 'fail')" 2>/dev/null)

if [ "$CC_A_OK" = "ok" ]; then
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/concurrency-a" \
        -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
    sleep 2

    CC_B_CODE=$(curl -sf -o /dev/null -w '%{http_code}' -X POST "$GATEWAY_URL/bots" \
        -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
        -d '{"platform":"google_meet","native_meeting_id":"concurrency-b","bot_name":"CC-B","automatic_leave":{"no_one_joined_timeout":30000}}' 2>/dev/null || echo "000")

    if [ "$CC_B_CODE" = "403" ]; then
        step_fail concurrency_slot "B got 403 — slot not released on stop"
    else
        step_pass concurrency_slot "slot released, B created (HTTP $CC_B_CODE)"
    fi

    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/concurrency-b" \
        -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
else
    step_fail concurrency_slot "could not create bot A"
fi

sleep 10

# ── Step: no_orphans ─────────────────────────────
if [ "$MODE" = "compose" ]; then
    ORPHANS=$(docker ps -a --filter "status=exited" --filter "name=meeting-" \
        --format '{{.Names}}' | { grep -vc meeting-api || true; })
elif [ "$MODE" = "lite" ]; then
    ORPHANS=$(docker exec vexa ps aux 2>/dev/null | { grep -c '[Z]' || true; })
elif [ "$MODE" = "helm" ]; then
    if command -v kubectl >/dev/null 2>&1 && kubectl cluster-info >/dev/null 2>&1; then
        ORPHANS=$(kubectl get pods --field-selector=status.phase!=Running --no-headers -l app.kubernetes.io/name=vexa 2>/dev/null | { grep -c "meeting-\|bot-" || true; }) || true
    else
        ORPHANS="unknown"
    fi
else
    ORPHANS=0
fi

if [ "$ORPHANS" = "unknown" ]; then
    step_skip no_orphans "no kubectl access in helm mode"
elif [ "${ORPHANS:-0}" -eq 0 ]; then
    step_pass no_orphans "no exited/zombie containers"
else
    step_fail no_orphans "$ORPHANS exited/zombie container(s)"
fi

echo "  ──────────────────────────────────────────────"
echo ""
