#!/usr/bin/env bash
# Launch a recorder bot into the meeting. Poll until active.
# Reads: .state/gateway_url, .state/api_token, .state/native_meeting_id, .state/meeting_platform
# Writes: .state/bot_id
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
NATIVE_ID=$(state_read native_meeting_id)
PLATFORM=$(state_read meeting_platform)

echo ""
echo "  bot"
echo "  ──────────────────────────────────────────────"

# ── 1. Launch recorder ────────────────────────────

echo "  launching recorder bot..."
RESP=$(http_post "$GATEWAY_URL/bots" \
    "{\"platform\":\"$PLATFORM\",\"native_meeting_id\":\"$NATIVE_ID\",\"bot_name\":\"Recorder\",\"transcribe_enabled\":true,\"automatic_leave\":{\"no_one_joined_timeout\":300000}}" \
    "$API_TOKEN")

BOT_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -z "$BOT_ID" ]; then
    fail "could not create bot"
    info "response: $RESP (HTTP $HTTP_CODE)"
    exit 1
fi

state_write bot_id "$BOT_ID"
pass "bot created: $BOT_ID"

# ── 2. Poll for status ───────────────────────────

echo "  polling status..."
PREV=""
for i in $(seq 1 30); do
    STATUS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | \
        python3 -c "
import sys,json
bots=json.load(sys.stdin).get('running_bots',[])
for b in bots:
    if b.get('native_meeting_id')=='$NATIVE_ID':
        print(b.get('meeting_status',b.get('status','')))
        break
" 2>/dev/null | head -1)

    if [ "$STATUS" != "$PREV" ]; then
        info "$(date +%H:%M:%S) $PREV → $STATUS"
        PREV="$STATUS"
    fi

    case "$STATUS" in
        active)
            pass "bot active in meeting"
            state_write bot_status active
            echo "  ──────────────────────────────────────────────"
            echo ""
            exit 0
            ;;
        awaiting_admission)
            echo ""
            echo "  ┌─────────────────────────────────────────┐"
            echo "  │  Bot is in the waiting room.             │"
            echo "  │  Admit it in the meeting UI, then wait.  │"
            echo "  └─────────────────────────────────────────┘"
            echo ""
            # Keep polling — human or auto-admit will handle it
            ;;
        failed|error|ended)
            fail "bot reached terminal state: $STATUS"
            exit 1
            ;;
    esac
    sleep 10
done

fail "bot did not reach active after 5 minutes (last: $STATUS)"
exit 1
