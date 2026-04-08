#!/usr/bin/env bash
# TTS reliability test: sends 10 speak commands to an active bot, then checks
# whether each one actually produced audio by looking at the transcript.
#
# Usage:
#   MEETING_URL="https://teams.microsoft.com/meet/..." make -C tests3 tts-reliability
#
# Requires: human in the meeting to admit the bot from the lobby.
#
# Reads: .state/gateway_url, .state/api_token
# Writes: .state/tts-reliability.json
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)

# ── Validate MEETING_URL ─────────────────────────
if [ -z "${MEETING_URL:-}" ]; then
    fail "MEETING_URL not set. Usage: MEETING_URL='https://teams.microsoft.com/meet/...' make -C tests3 tts-reliability"
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
echo "  tts-reliability"
echo "  ══════════════════════════════════════════════"
info "URL: $MEETING_URL"
info "platform: $PLATFORM"
info "native_meeting_id: $NATIVE_ID"

# ── Phrases and voices ──────────────────────────
PHRASES=(
    "Good morning everyone. Let us begin the quarterly review meeting."
    "Revenue increased by fifteen percent compared to the previous quarter."
    "Customer satisfaction scores have reached an all time high of ninety two percent."
    "We need to allocate additional resources to the engineering department."
    "The marketing campaign exceeded expectations with a thirty percent increase in leads."
    "Our product roadmap includes three major feature releases this quarter."
    "The security audit revealed no critical vulnerabilities in the infrastructure."
    "We should schedule a follow up meeting to discuss the budget proposal."
    "The partnership with the European team has been very productive this year."
    "Thank you all for your contributions. Let us reconvene next week."
)
VOICES=("alloy" "echo")
TOTAL=${#PHRASES[@]}

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
    -d "{\"meeting_url\":\"$MEETING_URL\",\"bot_name\":\"TTS-Reliability\",\"transcribe_enabled\":true,\"voice_agent_enabled\":true,\"automatic_leave\":{\"no_one_joined_timeout\":300000}}")
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

# ══════════════════════════════════════════════════
#  PHASE 3: Send 10 TTS commands
# ══════════════════════════════════════════════════

echo "  ── phase 3: send TTS (10 commands, 8s apart) ──"

SENT_OK=0
declare -a SEND_CODES
declare -a SEND_TIMES

for idx in $(seq 0 $((TOTAL - 1))); do
    TEXT="${PHRASES[$idx]}"
    VOICE="${VOICES[$((idx % 2))]}"
    TIMESTAMP=$(date '+%H:%M:%S')

    TTS_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
        "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID/speak" \
        -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
        -d "{\"text\":\"$TEXT\",\"voice\":\"$VOICE\"}" 2>/dev/null || echo "000")

    SEND_CODES[$idx]=$TTS_CODE
    SEND_TIMES[$idx]=$TIMESTAMP

    if [ "$TTS_CODE" = "202" ] || [ "$TTS_CODE" = "200" ]; then
        SENT_OK=$((SENT_OK + 1))
        info "[$((idx+1))/$TOTAL] $TIMESTAMP  $TEXT  voice=$VOICE  HTTP $TTS_CODE"
    else
        fail "[$((idx+1))/$TOTAL] $TIMESTAMP  $TEXT  voice=$VOICE  HTTP $TTS_CODE"
    fi

    # Wait 8s between commands (skip after the last one)
    if [ "$idx" -lt $((TOTAL - 1)) ]; then
        sleep 8
    fi
done

pass "TTS: $SENT_OK/$TOTAL speak commands accepted"

# ══════════════════════════════════════════════════
#  PHASE 4: Wait for pipeline, fetch transcript
# ══════════════════════════════════════════════════

echo "  ── phase 4: fetch transcript ──────────────────"

info "waiting 15s for pipeline..."
sleep 15

RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/transcripts/$PLATFORM/$NATIVE_ID" 2>/dev/null || echo '{}')

SEGMENTS=$(echo "$RESP" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    segs=d.get('segments',[]) if isinstance(d,dict) else d
    print(len(segs))
except: print(0)
" 2>/dev/null)

info "transcript: ${SEGMENTS:-0} segments"

# ══════════════════════════════════════════════════
#  PHASE 5: Fuzzy-match each phrase in transcript
# ══════════════════════════════════════════════════

echo "  ── phase 5: match phrases ─────────────────────"

# Build the phrases as a JSON array and pass to python for matching
PHRASES_JSON=$(python3 -c "
import json
phrases = $(printf '%s\n' "${PHRASES[@]}" | python3 -c "
import sys, json
print(json.dumps([line.strip() for line in sys.stdin]))
")
print(json.dumps(phrases))
")

MATCH_RESULT=$(echo "$RESP" | python3 -c "
import sys, json

phrases = $PHRASES_JSON
transcript = json.load(sys.stdin)
segments = transcript.get('segments', []) if isinstance(transcript, dict) else transcript

# Build full transcript text (lowered)
full_text = ' '.join(s.get('text', '') for s in segments).lower()

# Fuzzy match: check if key words from each phrase appear in transcript
results = []
for phrase in phrases:
    words = phrase.lower().split()
    # A phrase matches if all its words appear in the transcript text
    found = all(w in full_text for w in words)
    results.append({'phrase': phrase, 'found': found})

matched = sum(1 for r in results if r['found'])
print(json.dumps({'matched': matched, 'total': len(phrases), 'details': results}))
" 2>/dev/null)

MATCHED=$(echo "$MATCH_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['matched'])" 2>/dev/null || echo "0")

info "phrases found in transcript: $MATCHED/$TOTAL"

# Show which phrases were missing
echo "$MATCH_RESULT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for d in data['details']:
    status = 'found' if d['found'] else 'MISSING'
    print(f\"  {status:>7}  {d['phrase']}\")
" 2>/dev/null | while read -r line; do
    info "$line"
done

# ══════════════════════════════════════════════════
#  PHASE 6: Cleanup
# ══════════════════════════════════════════════════

echo "  ── phase 6: cleanup ─────────────────────────"

curl -sf -X DELETE "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
pass "bot stopped"

# ══════════════════════════════════════════════════
#  PHASE 7: Write results + verdict
# ══════════════════════════════════════════════════

echo "  ── results ────────────────────────────────────"

# Build send_codes JSON array
CODES_JSON="["
for idx in $(seq 0 $((TOTAL - 1))); do
    [ "$idx" -gt 0 ] && CODES_JSON+=","
    CODES_JSON+="${SEND_CODES[$idx]}"
done
CODES_JSON+="]"

python3 -c "
import json, sys

match_result = $MATCH_RESULT
send_codes = $CODES_JSON

accepted = sum(1 for c in send_codes if c in (200, 202))
matched = match_result['matched']
total = match_result['total']

result = {
    'platform': '$PLATFORM',
    'native_meeting_id': '$NATIVE_ID',
    'speak_accepted': f'{accepted}/{total}',
    'phrases_in_transcript': f'{matched}/{total}',
    'details': [],
    'pass': matched >= 8
}

for i, d in enumerate(match_result['details']):
    result['details'].append({
        'phrase': d['phrase'],
        'http_code': send_codes[i],
        'found_in_transcript': d['found']
    })

json.dump(result, sys.stdout, indent=2)
print()
" > "$STATE/tts-reliability.json"

info "results written to .state/tts-reliability.json"

pass "speak accepted: $SENT_OK/$TOTAL"
if [ "$MATCHED" -ge 8 ]; then
    pass "phrases in transcript: $MATCHED/$TOTAL (>= 8 required) -- PASS"
else
    fail "phrases in transcript: $MATCHED/$TOTAL (>= 8 required) -- FAIL"
    exit 1
fi

echo "  ══════════════════════════════════════════════"
echo ""
