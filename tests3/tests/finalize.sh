#!/usr/bin/env bash
# Stop all bots, verify clean shutdown, check no orphan containers.
# Reads: .state/gateway_url, .state/api_token, .state/native_meeting_id, .state/meeting_platform
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
NATIVE_ID=$(state_read native_meeting_id)
PLATFORM=$(state_read meeting_platform)

echo ""
echo "  finalize"
echo "  ──────────────────────────────────────────────"

# ── 1. Stop bots ─────────────────────────────────

echo "  stopping bots..."
http_get "$GATEWAY_URL/bots/status" "$API_TOKEN" > /dev/null
curl -sf -X DELETE "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID" \
    -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
pass "stop sent"

# ── 2. Wait ───────────────────────────────────────

echo "  waiting 15s for cleanup..."
sleep 15

# ── 3. Verify status ─────────────────────────────

STATUS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | \
    python3 -c "
import sys,json
bots=json.load(sys.stdin).get('running_bots',[])
active=[b for b in bots if b.get('native_meeting_id')=='$NATIVE_ID']
if active: print(active[0].get('status','?'))
else: print('gone')
" 2>/dev/null)

if [ "$STATUS" = "gone" ] || [ "$STATUS" = "completed" ]; then
    pass "bot stopped cleanly ($STATUS)"
else
    fail "bot still present after stop: $STATUS"
fi

# ── 4. Check orphan containers ────────────────────

MODE=$(state_read deploy_mode)
if [ "$MODE" = "compose" ]; then
    ORPHANS=$(docker ps -a --filter "name=meeting-" --filter "status=exited" \
        --format '{{.Names}}' | { grep -vc meeting-api || true; })
elif [ "$MODE" = "lite" ]; then
    ORPHANS=$(docker exec vexa ps aux 2>/dev/null | { grep -c '[Z]' || true; })
elif [ "$MODE" = "helm" ]; then
    ORPHANS=$(kubectl get pods --field-selector=status.phase!=Running --no-headers -l app.kubernetes.io/name=vexa 2>/dev/null | { grep -c "meeting-\|bot-" || true; })
else
    ORPHANS=0
fi

if [ "${ORPHANS:-0}" -eq 0 ]; then
    pass "no orphan containers"
else
    fail "$ORPHANS orphan container(s) found"
fi

# ── 5. Stop browser session if running ────────────

if state_exists session_token; then
    echo "  stopping browser session..."
    # Find the browser session's native_meeting_id
    BROWSER_NATIVE=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | \
        python3 -c "
import sys,json
bots=json.load(sys.stdin).get('running_bots',[])
for b in bots:
    if b.get('data',{}).get('mode')=='browser_session':
        print(b.get('native_meeting_id',''))
        break
" 2>/dev/null)

    if [ -n "$BROWSER_NATIVE" ]; then
        curl -sf -X DELETE "$GATEWAY_URL/bots/browser_session/$BROWSER_NATIVE" \
            -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
        pass "browser session stopped"
    fi
fi

echo "  ──────────────────────────────────────────────"
echo ""
