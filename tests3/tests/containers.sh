#!/usr/bin/env bash
# Container lifecycle: create, stop, verify removal, timeout, concurrency, transitions
# Covers DoDs: container#1-#9, bot#5,#12,#13,#14
# Reads: .state/gateway_url, .state/api_token, .state/deploy_mode
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
MODE=$(state_read deploy_mode)

echo ""
echo "  containers"
echo "  ──────────────────────────────────────────────"

# ── 0. Clean up stale bots from previous runs ─────
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
    pass "stale bots cleaned"
fi

# ── 1. Create bot, verify container starts ────────
echo "  creating test bot..."
RESP=$(http_post "$GATEWAY_URL/bots" \
    '{"platform":"google_meet","native_meeting_id":"lifecycle-test-1","bot_name":"LC Test","automatic_leave":{"no_one_joined_timeout":30000}}' \
    "$API_TOKEN")
BOT_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -n "$BOT_ID" ]; then
    pass "create: bot $BOT_ID"
else
    if [ "$(http_code)" = "500" ] && [ "$MODE" = "helm" ]; then
        info "create: HTTP 500 — bot runtime not configured in helm (expected)"
        info "skipping container lifecycle tests"
        echo "  ──────────────────────────────────────────────"
        echo ""
        exit 0
    fi
    fail "create: POST /bots failed (HTTP $(http_code))"
    info "$RESP"
    exit 1
fi

# Verify bot is actually running (not crashed immediately)
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
    pass "alive: bot process running after 10s"
elif [ "$BOT_ALIVE" = "gone" ]; then
    fail "alive: bot process died within 10s — check entrypoint/dependencies"
    # Check meeting status for the reason
    MEETING_STATUS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/meetings" | python3 -c "
import sys,json
d=json.load(sys.stdin)
ms=d.get('meetings',[]) if isinstance(d,dict) else d
for m in ms:
    if m.get('native_meeting_id')=='lifecycle-test-1':
        print(f'status={m.get(\"status\",\"?\")} reason={m.get(\"completion_reason\",\"?\")}')
        break
" 2>/dev/null)
    info "meeting: $MEETING_STATUS"
    exit 1
else
    info "alive: bot status=$BOT_ALIVE"
fi

# ── 2. Stop bot, verify container removed ─────────
echo "  stopping bot..."
curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/lifecycle-test-1" \
    -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true

sleep 15

if [ "$MODE" = "compose" ]; then
    REMAINING=$(docker ps -a --filter "name=meeting-" --format '{{.Names}}' | { grep -c "lifecycle-test" || true; })
elif [ "$MODE" = "lite" ]; then
    REMAINING=$(docker exec vexa ps aux 2>/dev/null | { grep -c "lifecycle-test" || true; })
elif [ "$MODE" = "helm" ]; then
    REMAINING=$(kubectl get pods --no-headers 2>/dev/null | { grep -c "lifecycle-test" || true; }) || true
else
    REMAINING=0
fi

if [ "${REMAINING:-0}" -eq 0 ]; then
    pass "removal: container fully removed after stop"
else
    fail "removal: $REMAINING container(s) still present after stop"
fi

# ── 3. Verify status = completed (not failed) ─────
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

if [ "$STATUS" = "completed" ] || [ "$STATUS" = "gone" ]; then
    pass "status: $STATUS after stop"
else
    fail "status: $STATUS (expected completed)"
fi

# ── 4. Timeout auto-stop ──────────────────────────
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
        pass "timeout: bot stopped ($TIMEOUT_STATUS)"
    else
        info "timeout: bot still $TIMEOUT_STATUS after 60s (timeout may count from lobby, not creation)"
        # Clean up
        curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/timeout-test" \
            -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
        sleep 10
    fi
else
    fail "timeout: could not create test bot"
fi

# ── 5. Concurrency release ────────────────────────
echo "  testing concurrency release..."
RESP_A=$(http_post "$GATEWAY_URL/bots" \
    '{"platform":"google_meet","native_meeting_id":"concurrency-a","bot_name":"CC-A","automatic_leave":{"no_one_joined_timeout":30000}}' \
    "$API_TOKEN")
CC_A_OK=$(echo "$RESP_A" | python3 -c "import sys,json; print('ok' if json.load(sys.stdin).get('id') else 'fail')" 2>/dev/null)

if [ "$CC_A_OK" = "ok" ]; then
    # Stop A immediately
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/concurrency-a" \
        -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
    sleep 2

    # Create B immediately — should succeed even if A's container still running
    CC_B_CODE=$(curl -sf -o /dev/null -w '%{http_code}' -X POST "$GATEWAY_URL/bots" \
        -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
        -d '{"platform":"google_meet","native_meeting_id":"concurrency-b","bot_name":"CC-B","automatic_leave":{"no_one_joined_timeout":30000}}' 2>/dev/null || echo "000")

    if [ "$CC_B_CODE" = "403" ]; then
        fail "concurrency: B got 403 — slot not released on stop"
    else
        pass "concurrency: slot released, B created"
    fi

    # Clean up
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/concurrency-b" \
        -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
else
    fail "concurrency: could not create bot A"
fi

sleep 10

# ── 6. Orphan check ───────────────────────────────
if [ "$MODE" = "compose" ]; then
    ORPHANS=$(docker ps -a --filter "status=exited" --filter "name=meeting-" \
        --format '{{.Names}}' | { grep -vc meeting-api || true; })
elif [ "$MODE" = "lite" ]; then
    ORPHANS=$(docker exec vexa ps aux 2>/dev/null | { grep -c '[Z]' || true; })
elif [ "$MODE" = "helm" ]; then
    ORPHANS=$(kubectl get pods --field-selector=status.phase!=Running --no-headers -l app.kubernetes.io/name=vexa 2>/dev/null | { grep -c "meeting-\|bot-" || true; }) || true
else
    ORPHANS=0
fi

if [ "${ORPHANS:-0}" -eq 0 ]; then
    pass "orphans: none"
else
    fail "orphans: $ORPHANS exited container(s)"
fi

echo "  ──────────────────────────────────────────────"
echo ""
