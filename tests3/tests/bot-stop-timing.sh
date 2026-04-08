#!/usr/bin/env bash
# Bot stop lifecycle timing test.
# Creates a bot on a meeting, waits for active, stops it, and measures
# the time each lifecycle state transition takes.
#
# Usage:
#   MEETING_URL="https://teams.microsoft.com/meet/..." make -C tests3 bot-stop-timing
#
# Requires: human in the meeting to admit the bot from the lobby.
#
# Reads: .state/gateway_url, .state/api_token
# Writes: .state/bot-stop-timing.json
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)

# ── Validate MEETING_URL ─────────────────────────
if [ -z "${MEETING_URL:-}" ]; then
    fail "MEETING_URL not set. Usage: MEETING_URL='https://teams.microsoft.com/meet/...' make -C tests3 bot-stop-timing"
    exit 1
fi

# ── Detect platform and extract native_meeting_id ─
if echo "$MEETING_URL" | grep -q 'teams.microsoft.com'; then
    PLATFORM="teams"
    NATIVE_ID=$(echo "$MEETING_URL" | grep -oP '/meet/\K\d{10,15}')
elif echo "$MEETING_URL" | grep -q 'meet.google.com'; then
    PLATFORM="gmeet"
    NATIVE_ID=$(echo "$MEETING_URL" | grep -oP 'meet\.google\.com/\K[a-z-]+')
else
    fail "unsupported meeting URL: $MEETING_URL"
    exit 1
fi

if [ -z "$NATIVE_ID" ]; then
    fail "could not extract native_meeting_id from: $MEETING_URL"
    exit 1
fi

echo ""
echo "  bot-stop-timing"
echo "  ══════════════════════════════════════════════"
info "URL: $MEETING_URL"
info "platform: $PLATFORM"
info "native_meeting_id: $NATIVE_ID"

# ── Cleanup stale bots ──────────────────────────
info "cleaning stale bots..."
curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    mid=b.get('native_meeting_id','')
    p=b.get('platform','teams')
    print(f'{p}/{mid}')
" 2>/dev/null | while read -r bp; do
    curl -sf -X DELETE "$GATEWAY_URL/bots/$bp" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
done
sleep 5
pass "stale bots cleaned"

# ══════════════════════════════════════════════════
#  PHASE 1: Create bot
# ══════════════════════════════════════════════════

echo "  ── phase 1: create bot ────────────────────────"

BOT_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -d "{\"meeting_url\":\"$MEETING_URL\",\"bot_name\":\"StopTimer\",\"transcribe_enabled\":true,\"automatic_leave\":{\"no_one_joined_timeout\":300000}}")
BOT_ID=$(echo "$BOT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -z "$BOT_ID" ]; then
    fail "bot creation failed: $BOT_RESP"
    exit 1
fi
pass "bot created: id=$BOT_ID"

# ══════════════════════════════════════════════════
#  PHASE 2: Wait for active (human must admit)
# ══════════════════════════════════════════════════

echo "  ── phase 2: wait for active ───────────────────"
echo ""
echo "  ┌──────────────────────────────────────────────┐"
echo "  |  Bot is waiting in the meeting lobby.         |"
echo "  |  Admit it in the meeting UI.                  |"
echo "  |  Polling until active...                      |"
echo "  └──────────────────────────────────────────────┘"
echo ""

BOT_ACTIVE=0
for i in $(seq 1 60); do
    STATUS_RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" 2>/dev/null || echo '{}')
    ACTIVE_COUNT=$(echo "$STATUS_RESP" | python3 -c "
import sys,json
bots=[b for b in json.load(sys.stdin).get('running_bots',[])
      if b.get('native_meeting_id')=='$NATIVE_ID' and b.get('meeting_status','')=='active']
print(len(bots))" 2>/dev/null)
    info "[$i] active: ${ACTIVE_COUNT:-0}"
    if [ "${ACTIVE_COUNT:-0}" -ge 1 ]; then
        BOT_ACTIVE=1
        break
    fi
    sleep 5
done

if [ "$BOT_ACTIVE" -ne 1 ]; then
    fail "bot never became active after 5 min"
    exit 1
fi
pass "bot is active"

# Get the meeting_id for later polling
MEETING_ID=$(echo "$STATUS_RESP" | python3 -c "
import sys,json
bots=[b for b in json.load(sys.stdin).get('running_bots',[])
      if b.get('native_meeting_id')=='$NATIVE_ID']
print(bots[0].get('meeting_id','') if bots else '')" 2>/dev/null)
info "meeting_id: $MEETING_ID"

# ══════════════════════════════════════════════════
#  PHASE 3: Stop bot and measure timing
# ══════════════════════════════════════════════════

echo "  ── phase 3: stop and measure ─────────────────"

# Record T0 immediately before DELETE
T0=$(python3 -c "import time; print(time.time())")
T0_HUMAN=$(date '+%H:%M:%S')
info "T0 (delete sent): $T0_HUMAN"

# Send DELETE
DEL_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE \
    "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID" \
    -H "X-API-Key: $API_TOKEN" 2>/dev/null || echo "000")
info "DELETE response: HTTP $DEL_CODE"

if [ "$DEL_CODE" != "200" ] && [ "$DEL_CODE" != "202" ] && [ "$DEL_CODE" != "204" ]; then
    fail "DELETE /bots/$PLATFORM/$NATIVE_ID failed: HTTP $DEL_CODE"
    exit 1
fi

# Timestamp placeholders
T1=""  # status changed to stopping
T2=""  # bot callback arrived (status_transition[])
T4=""  # status changed to completed
T5=""  # post-meeting tasks ran (end_time exists)

TIMEOUT=180
POLL_INTERVAL=2
ELAPSED=0

info "polling every ${POLL_INTERVAL}s (timeout ${TIMEOUT}s)..."

while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    sleep "$POLL_INTERVAL"
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
    NOW=$(python3 -c "import time; print(time.time())")
    NOW_HUMAN=$(date '+%H:%M:%S')

    # Poll bots/status to check meeting_status
    BOT_STATUS_RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" 2>/dev/null || echo '{}')
    BOT_STATUS=$(echo "$BOT_STATUS_RESP" | python3 -c "
import sys,json
bots=[b for b in json.load(sys.stdin).get('running_bots',[])
      if b.get('native_meeting_id')=='$NATIVE_ID']
print(bots[0].get('meeting_status','gone') if bots else 'gone')" 2>/dev/null)

    # Poll meeting data if we have meeting_id
    MEETING_RESP=""
    MEETING_STATUS=""
    HAS_TRANSITIONS=""
    HAS_END_TIME=""
    if [ -n "$MEETING_ID" ]; then
        MEETING_RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/meetings/$MEETING_ID" 2>/dev/null || echo '{}')
        MEETING_DATA=$(echo "$MEETING_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
status=d.get('status','')
transitions=d.get('status_transitions',[]) or d.get('status_transition',[]) or []
has_transitions='yes' if len(transitions)>0 else 'no'
end_time=d.get('end_time','') or ''
has_end_time='yes' if end_time else 'no'
print(f'{status}|{has_transitions}|{has_end_time}')
" 2>/dev/null || echo "||")
        MEETING_STATUS=$(echo "$MEETING_DATA" | cut -d'|' -f1)
        HAS_TRANSITIONS=$(echo "$MEETING_DATA" | cut -d'|' -f2)
        HAS_END_TIME=$(echo "$MEETING_DATA" | cut -d'|' -f3)
    fi

    # Record T1: stopping
    if [ -z "$T1" ] && { [ "$BOT_STATUS" = "stopping" ] || [ "$MEETING_STATUS" = "stopping" ]; }; then
        T1=$NOW
        T1_HUMAN=$NOW_HUMAN
        T1_DELTA=$(python3 -c "print(f'{$NOW - $T0:.1f}')")
        info "T1 (stopping):   $T1_HUMAN  +${T1_DELTA}s"
    fi

    # Record T2: status_transition[] appeared
    if [ -z "$T2" ] && [ "$HAS_TRANSITIONS" = "yes" ]; then
        T2=$NOW
        T2_HUMAN=$NOW_HUMAN
        T2_DELTA=$(python3 -c "print(f'{$NOW - $T0:.1f}')")
        info "T2 (callback):   $T2_HUMAN  +${T2_DELTA}s"
    fi

    # Record T4: completed
    if [ -z "$T4" ] && { [ "$BOT_STATUS" = "gone" ] || [ "$MEETING_STATUS" = "completed" ] || [ "$MEETING_STATUS" = "ended" ]; }; then
        # Only record if we already saw stopping or have been waiting > 5s
        if [ -n "$T1" ] || [ "$ELAPSED" -gt 5 ]; then
            T4=$NOW
            T4_HUMAN=$NOW_HUMAN
            T4_DELTA=$(python3 -c "print(f'{$NOW - $T0:.1f}')")
            info "T4 (completed):  $T4_HUMAN  +${T4_DELTA}s"
        fi
    fi

    # Record T5: end_time exists (post-meeting tasks)
    if [ -z "$T5" ] && [ "$HAS_END_TIME" = "yes" ]; then
        T5=$NOW
        T5_HUMAN=$NOW_HUMAN
        T5_DELTA=$(python3 -c "print(f'{$NOW - $T0:.1f}')")
        info "T5 (end_time):   $T5_HUMAN  +${T5_DELTA}s"
    fi

    # Log progress
    if [ -z "$T4" ]; then
        info "[${ELAPSED}s] bot_status=$BOT_STATUS meeting_status=$MEETING_STATUS"
    fi

    # Done once we have T4 and T5 (or at least T4)
    if [ -n "$T4" ] && [ -n "$T5" ]; then
        info "all timestamps recorded"
        break
    fi
    # If T4 is set but T5 is not, keep polling for end_time up to 30s more
    if [ -n "$T4" ]; then
        T4_AGE=$(python3 -c "print(int($NOW - $T4))")
        if [ "$T4_AGE" -gt 30 ]; then
            info "T5 not seen 30s after T4, moving on"
            break
        fi
    fi
done

# ══════════════════════════════════════════════════
#  PHASE 4: Report
# ══════════════════════════════════════════════════

echo ""
echo "  ── timing report ────────────────────────────"
echo ""

report_line() {
    local label="$1" ts="$2"
    if [ -n "$ts" ]; then
        local delta
        delta=$(python3 -c "print(f'{$ts - $T0:.1f}')")
        printf '  %-25s %ss\n' "$label" "$delta"
    else
        printf '  %-25s %s\n' "$label" "(not observed)"
    fi
}

report_line "T1-T0 (-> stopping)" "$T1"
report_line "T2-T0 (callback)" "$T2"
report_line "T4-T0 (-> completed)" "$T4"
report_line "T5-T0 (end_time set)" "$T5"

echo ""

# ── Write JSON results ──────────────────────────
python3 -c "
import json, sys

def delta(ts, t0):
    if ts and t0:
        return round(float(ts) - float(t0), 1)
    return None

t0 = '$T0' or None
t1 = '$T1' or None
t2 = '$T2' or None
t4 = '$T4' or None
t5 = '$T5' or None

result = {
    'platform': '$PLATFORM',
    'native_meeting_id': '$NATIVE_ID',
    'meeting_id': '$MEETING_ID',
    'T0': float(t0) if t0 else None,
    'T1_stopping': delta(t1, t0),
    'T2_callback': delta(t2, t0),
    'T4_completed': delta(t4, t0),
    'T5_end_time': delta(t5, t0),
    'pass': delta(t4, t0) is not None and delta(t4, t0) < 120
}

json.dump(result, sys.stdout, indent=2)
print()
" > "$STATE/bot-stop-timing.json"

info "results written to .state/bot-stop-timing.json"

# ── Pass/fail ───────────────────────────────────
if [ -n "$T4" ]; then
    T4_DELTA=$(python3 -c "print(f'{$T4 - $T0:.1f}')")
    T4_INT=$(python3 -c "print(int($T4 - $T0))")
    if [ "$T4_INT" -lt 120 ]; then
        pass "bot stopped in ${T4_DELTA}s (< 120s)"
    else
        fail "bot stop took ${T4_DELTA}s (>= 120s)"
        exit 1
    fi
else
    fail "bot never reached completed within ${TIMEOUT}s"
    exit 1
fi

echo "  ══════════════════════════════════════════════"
echo ""
