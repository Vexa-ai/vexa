#!/usr/bin/env bash
# Full Teams meeting TTS test: join meeting → launch recorder → launch N speakers →
# human admits bots → send TTS → fetch transcript → score → cleanup.
#
# Usage:
#   TEAMS_MEETING_URL="https://teams.microsoft.com/meet/365488377316481?p=ryf9yhbfrc7MpM7kNv" \
#     make -C tests3 meeting-tts-teams
#
# Requires: human in the Teams meeting to admit bots from lobby.
#
# Reads: .state/gateway_url, .state/api_token, .state/admin_url, .state/admin_token
# Writes: .state/native_meeting_id, .state/meeting_platform, .state/meeting_url, .state/segments, .state/quality
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
ADMIN_URL=$(state_read admin_url)
ADMIN_TOKEN=$(state_read admin_token)

# ── Validate TEAMS_MEETING_URL ───────────────────
if [ -z "$TEAMS_MEETING_URL" ]; then
    fail "TEAMS_MEETING_URL not set. Usage: TEAMS_MEETING_URL='https://teams.microsoft.com/meet/...' make -C tests3 meeting-tts-teams"
    exit 1
fi

# Extract native_meeting_id and passcode from URL
NATIVE_ID=$(echo "$TEAMS_MEETING_URL" | grep -oP '/meet/\K\d{10,15}')
PASSCODE=$(echo "$TEAMS_MEETING_URL" | grep -oP '[?&]p=\K[A-Za-z0-9]+')

if [ -z "$NATIVE_ID" ]; then
    fail "Could not extract numeric meeting ID from: $TEAMS_MEETING_URL"
    exit 1
fi
if [ -z "$PASSCODE" ]; then
    fail "Could not extract passcode from: $TEAMS_MEETING_URL"
    exit 1
fi

# Ground truth: speaker|text
GROUND_TRUTH=(
    "Alice|Good morning everyone. Let's review the quarterly numbers."
    "Bob|Revenue increased by fifteen percent compared to last quarter."
    "Alice|Customer satisfaction score is ninety two percent."
    "Bob|The marketing budget needs to be increased by twenty percent."
)

echo ""
echo "  meeting-tts-teams"
echo "  ══════════════════════════════════════════════"
info "URL: $TEAMS_MEETING_URL"
info "native_meeting_id: $NATIVE_ID"
info "passcode: ${PASSCODE:0:4}..."

# ── Cleanup stale bots ───────────────────────────
info "cleaning stale bots..."

curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    mid=b.get('native_meeting_id','')
    p=b.get('platform','teams')
    mode=b.get('data',{}).get('mode','')
    if mode=='browser_session': print(f'browser_session/{mid}')
    else: print(f'{p}/{mid}')
" 2>/dev/null | while read -r bp; do
    curl -sf -X DELETE "$GATEWAY_URL/bots/$bp" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
done

for SPEAKER_EMAIL in $(printf '%s\n' "${GROUND_TRUTH[@]}" | cut -d'|' -f1 | sort -u | tr '[:upper:]' '[:lower:]'); do
    USER_RESP=$(curl -s "$ADMIN_URL/admin/users/email/${SPEAKER_EMAIL}@vexa.ai" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" -w "\n%{http_code}" 2>/dev/null)
    USER_HTTP=$(echo "$USER_RESP" | tail -1)
    USER_BODY=$(echo "$USER_RESP" | head -n -1)
    [ "$USER_HTTP" != "200" ] && continue

    USER_ID=$(echo "$USER_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
    [ -z "$USER_ID" ] && continue

    TOKEN=$(curl -s -X POST "$ADMIN_URL/admin/users/$USER_ID/tokens?scopes=bot&name=cleanup" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)
    [ -z "$TOKEN" ] && continue

    curl -sf -H "X-API-Key: $TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    print(b.get('platform','teams')+'/'+b.get('native_meeting_id',''))
" 2>/dev/null | while read -r bp; do
        curl -sf -X DELETE "$GATEWAY_URL/bots/$bp" -H "X-API-Key: $TOKEN" > /dev/null 2>&1 || true
    done
done

sleep 10
pass "stale bots cleaned"

# ══════════════════════════════════════════════════
#  PHASE 1: Write state (meeting already exists)
# ══════════════════════════════════════════════════

echo "  ── phase 1: meeting state ───────────────────"

state_write native_meeting_id "$NATIVE_ID"
state_write meeting_platform "teams"
state_write meeting_url "$TEAMS_MEETING_URL"
pass "meeting: teams/$NATIVE_ID"

# ══════════════════════════════════════════════════
#  PHASE 2: Launch recorder + speaker bots
# ══════════════════════════════════════════════════

echo "  ── phase 2: launch bots ─────────────────────"

declare -A SPEAKER_TOKENS
SPEAKERS=($(printf '%s\n' "${GROUND_TRUTH[@]}" | cut -d'|' -f1 | sort -u))

# Recorder bot (test user — transcribe_enabled)
info "launching recorder..."
REC_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -d "{\"meeting_url\":\"$TEAMS_MEETING_URL\",\"bot_name\":\"Recorder\",\"transcribe_enabled\":true,\"automatic_leave\":{\"no_one_joined_timeout\":300000}}")
RECORDER_ID=$(echo "$REC_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [ -z "$RECORDER_ID" ]; then
    fail "recorder creation failed: $REC_RESP"
    exit 1
fi
state_write bot_id "$RECORDER_ID"
pass "recorder: id=$RECORDER_ID"

# Speaker bots (one per unique speaker, each a separate user)
for SPEAKER in "${SPEAKERS[@]}"; do
    SPEAKER_LOWER=$(echo "$SPEAKER" | tr '[:upper:]' '[:lower:]')
    SPEAKER_EMAIL="${SPEAKER_LOWER}@vexa.ai"

    USER_RESP=$(curl -s "$ADMIN_URL/admin/users/email/$SPEAKER_EMAIL" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" -w "\n%{http_code}" 2>/dev/null)
    USER_HTTP=$(echo "$USER_RESP" | tail -1)
    USER_BODY=$(echo "$USER_RESP" | head -n -1)

    if [ "$USER_HTTP" = "200" ]; then
        USER_ID=$(echo "$USER_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
        curl -s -X PATCH "$ADMIN_URL/admin/users/$USER_ID" \
            -H "X-Admin-API-Key: $ADMIN_TOKEN" -H "Content-Type: application/json" \
            -d '{"max_concurrent_bots":3}' > /dev/null 2>&1
    else
        USER_BODY=$(curl -s -X POST "$ADMIN_URL/admin/users" \
            -H "X-Admin-API-Key: $ADMIN_TOKEN" -H "Content-Type: application/json" \
            -d "{\"email\":\"$SPEAKER_EMAIL\",\"name\":\"$SPEAKER\",\"max_concurrent_bots\":3}" 2>/dev/null)
        USER_ID=$(echo "$USER_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
    fi

    TOKEN=$(curl -s -X POST "$ADMIN_URL/admin/users/$USER_ID/tokens?scopes=bot,browser,tx&name=spk-$SPEAKER_LOWER" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)

    SPEAKER_TOKENS[$SPEAKER]=$TOKEN

    BOT_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
        -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
        -d "{\"meeting_url\":\"$TEAMS_MEETING_URL\",\"bot_name\":\"$SPEAKER\",\"voice_agent_enabled\":true,\"automatic_leave\":{\"no_one_joined_timeout\":300000}}")
    BOT_ID=$(echo "$BOT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

    if [ -n "$BOT_ID" ]; then
        pass "speaker $SPEAKER: user=$USER_ID bot=$BOT_ID"
    else
        fail "speaker $SPEAKER: creation failed: $BOT_RESP"
    fi
done

TOTAL_BOTS=$(( 1 + ${#SPEAKERS[@]} ))
info "$TOTAL_BOTS bots launched (1 recorder + ${#SPEAKERS[@]} speakers)"

# ══════════════════════════════════════════════════
#  PHASE 3: Wait for admission (human admits from Teams)
# ══════════════════════════════════════════════════

echo "  ── phase 3: admit bots ────────────────────────"
echo ""
echo "  ┌──────────────────────────────────────────────┐"
echo "  |  $TOTAL_BOTS bots waiting in the Teams lobby.          |"
echo "  |  Admit them in the Teams meeting UI.          |"
echo "  |  Polling until all are active...              |"
echo "  └──────────────────────────────────────────────┘"
echo ""

ALL_TOKENS=("$API_TOKEN")
for SPEAKER in "${SPEAKERS[@]}"; do
    ALL_TOKENS+=("${SPEAKER_TOKENS[$SPEAKER]}")
done

for i in $(seq 1 60); do
    ACTIVE=0
    for TK in "${ALL_TOKENS[@]}"; do
        A=$(curl -sf -H "X-API-Key: $TK" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
bots=[b for b in json.load(sys.stdin).get('running_bots',[]) if b.get('native_meeting_id')=='$NATIVE_ID' and b.get('meeting_status','')=='active']
print(len(bots))" 2>/dev/null)
        ACTIVE=$(( ACTIVE + ${A:-0} ))
    done
    info "[$i] $ACTIVE/$TOTAL_BOTS active"
    [ "$ACTIVE" -ge "$TOTAL_BOTS" ] && break
    sleep 5
done

if [ "$ACTIVE" -ge "$TOTAL_BOTS" ]; then
    pass "all $TOTAL_BOTS bots active"
else
    fail "only $ACTIVE/$TOTAL_BOTS bots active after 5 min"
    exit 1
fi

# ══════════════════════════════════════════════════
#  PHASE 4: Send TTS utterances
# ══════════════════════════════════════════════════

echo "  ── phase 4: send TTS ────────────────────────"

SENT=0
for entry in "${GROUND_TRUTH[@]}"; do
    SPEAKER=$(echo "$entry" | cut -d'|' -f1)
    TEXT=$(echo "$entry" | cut -d'|' -f2-)
    TOKEN=${SPEAKER_TOKENS[$SPEAKER]}

    TTS_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
        "$GATEWAY_URL/bots/teams/$NATIVE_ID/speak" \
        -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
        -d "{\"text\":\"$TEXT\",\"voice\":\"alloy\"}" 2>/dev/null || echo "000")

    if [ "$TTS_CODE" = "202" ] || [ "$TTS_CODE" = "200" ]; then
        SENT=$((SENT + 1))
        info "$SPEAKER: ${TEXT:0:50}..."
    else
        fail "$SPEAKER: TTS failed (HTTP $TTS_CODE)"
    fi
    sleep 10
done

if [ "$SENT" -eq "${#GROUND_TRUTH[@]}" ]; then
    pass "TTS: $SENT/${#GROUND_TRUTH[@]} utterances sent"
else
    fail "TTS: only $SENT/${#GROUND_TRUTH[@]} sent"
fi

# ══════════════════════════════════════════════════
#  PHASE 5: Fetch transcript + score
# ══════════════════════════════════════════════════

echo "  ── phase 5: transcript ──────────────────────"

info "waiting 30s for pipeline..."
sleep 30

RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/transcripts/teams/$NATIVE_ID")
SEGMENTS=$(echo "$RESP" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    segs=d.get('segments',[]) if isinstance(d,dict) else d
    print(len(segs))
except: print(0)
" 2>/dev/null)

state_write segments "${SEGMENTS:-0}"

if [ "${SEGMENTS:-0}" -gt 0 ]; then
    pass "transcript: $SEGMENTS segments"

    QUALITY=$(echo "$RESP" | python3 -c "
import sys,json
gt_phrases=['good morning everyone','revenue increased','customer satisfaction','marketing budget']
d=json.load(sys.stdin)
segs=d.get('segments',[]) if isinstance(d,dict) else d
texts=' '.join(s.get('text','') for s in segs).lower()
matched=sum(1 for g in gt_phrases if g in texts)
speakers=set(s.get('speaker','Unknown') for s in segs)
speakers.discard('Unknown')
print(f'phrases={matched}/{len(gt_phrases)} speakers={len(speakers)}')
" 2>/dev/null)
    state_write quality "$QUALITY"
    pass "quality: $QUALITY"
else
    fail "transcript: 0 segments — recorder did not capture audio"
    info "check: are speaker bots' TTS actually playing audio into the meeting?"
    info "check: is the recorder's transcribe_enabled working?"
fi

# ══════════════════════════════════════════════════
#  PHASE 6: Cleanup
# ══════════════════════════════════════════════════

echo "  ── phase 6: cleanup ─────────────────────────"

curl -sf -X DELETE "$GATEWAY_URL/bots/teams/$NATIVE_ID" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
for SPEAKER in "${SPEAKERS[@]}"; do
    TOKEN=${SPEAKER_TOKENS[$SPEAKER]}
    curl -sf -X DELETE "$GATEWAY_URL/bots/teams/$NATIVE_ID" -H "X-API-Key: $TOKEN" > /dev/null 2>&1 || true
done

pass "cleanup: all bots stopped"

echo "  ══════════════════════════════════════════════"
echo ""
