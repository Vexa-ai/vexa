#!/usr/bin/env bash
# Full Zoom SDK meeting TTS test: join meeting -> launch recorder -> launch N
# speakers -> wait for admission -> send TTS -> fetch transcript -> score ->
# cleanup.
#
# Pack G of release 260422-zoom-sdk. Mirrors meeting-tts-teams.sh shape
# so the three realtime-transcription platforms (gmeet, msteams, zoom-sdk)
# follow the same test contract.
#
# Usage:
#   ZOOM_MEETING_URL="https://zoom.us/j/12345678901?pwd=..." \
#     make -C tests3 meeting-tts-zoom-sdk
#
# Requires:
#   - ZOOM_CLIENT_ID + ZOOM_CLIENT_SECRET on meeting-api (pre-flight
#     rejects otherwise -- Pack D).
#   - The Zoom meeting must be hosted by the SAME Zoom account that owns
#     the Marketplace SDK app. Unpublished apps cannot join external
#     meetings (SDK error code 63). See
#     services/vexa-bot/docs/zoom-sdk-setup.md section 6.
#   - Host account setting "Auto approve permission requests" ON for both
#     internal + external participants (otherwise the bot's privilege-
#     retry loop eventually times out with a clear error).
#
# Reads: .state/gateway_url, .state/api_token, .state/admin_url, .state/admin_token
# Writes: .state/native_meeting_id, .state/meeting_platform, .state/meeting_url,
#         .state/segments, .state/quality
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
ADMIN_URL=$(state_read admin_url)
ADMIN_TOKEN=$(state_read admin_token)

if [ -z "$ZOOM_MEETING_URL" ]; then
    fail "ZOOM_MEETING_URL not set. Usage: ZOOM_MEETING_URL='https://zoom.us/j/12345678901?pwd=...' make -C tests3 meeting-tts-zoom-sdk"
    exit 1
fi

NATIVE_ID=$(echo "$ZOOM_MEETING_URL" | grep -oP '/j/\K\d{9,11}')
PASSCODE=$(echo "$ZOOM_MEETING_URL" | grep -oP '[?&]pwd=\K[A-Za-z0-9._-]+')

if [ -z "$NATIVE_ID" ]; then
    fail "Could not extract 9-11 digit meeting ID from: $ZOOM_MEETING_URL"
    exit 1
fi
if [ -z "$PASSCODE" ]; then
    info "No pwd= in URL; assuming passcode-less meeting (short-link flow)."
fi

GROUND_TRUTH=(
    "Alice|Good morning everyone. Let's review the quarterly numbers."
    "Bob|Revenue increased by fifteen percent compared to last quarter."
    "Alice|Customer satisfaction score is ninety two percent."
    "Bob|The marketing budget needs to be increased by twenty percent."
)

echo ""
echo "  meeting-tts-zoom-sdk"
echo "  =============================================="
info "URL: $ZOOM_MEETING_URL"
info "native_meeting_id: $NATIVE_ID"
[ -n "$PASSCODE" ] && info "passcode: ${PASSCODE:0:4}..."

info "cleaning stale bots..."

curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    mid=b.get('native_meeting_id','')
    p=b.get('platform','zoom_sdk')
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
    print(b.get('platform','zoom_sdk')+'/'+b.get('native_meeting_id',''))
" 2>/dev/null | while read -r bp; do
        curl -sf -X DELETE "$GATEWAY_URL/bots/$bp" -H "X-API-Key: $TOKEN" > /dev/null 2>&1 || true
    done
done

sleep 10
pass "stale bots cleaned"

echo "  -- phase 1: meeting state --"

state_write native_meeting_id "$NATIVE_ID"
state_write meeting_platform "zoom_sdk"
state_write meeting_url "$ZOOM_MEETING_URL"
pass "meeting: zoom_sdk/$NATIVE_ID"

echo "  -- phase 2: launch bots --"

declare -A SPEAKER_TOKENS
SPEAKERS=($(printf '%s\n' "${GROUND_TRUTH[@]}" | cut -d'|' -f1 | sort -u))

# Build bot create body (handles optional passcode cleanly)
_make_bot_body() {
    local bot_name="$1"
    local extra="$2"
    python3 -c "
import json, os
body = {
    'platform': 'zoom_sdk',
    'native_meeting_id': os.environ['BOT_NATIVE_ID'],
    'bot_name': os.environ['BOT_NAME'],
    'automatic_leave': {'no_one_joined_timeout': 300000},
}
passcode = os.environ.get('BOT_PASSCODE', '')
if passcode:
    body['passcode'] = passcode
extra = os.environ.get('BOT_EXTRA', '')
if extra:
    body.update(json.loads(extra))
print(json.dumps(body))
"
}

info "launching recorder..."
BOT_NATIVE_ID="$NATIVE_ID" BOT_NAME="Recorder" BOT_PASSCODE="$PASSCODE" \
    BOT_EXTRA='{"transcribe_enabled": true}' \
    REC_BODY=$(_make_bot_body)
REC_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -d "$REC_BODY")
RECORDER_ID=$(echo "$REC_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [ -z "$RECORDER_ID" ]; then
    fail "recorder creation failed: $REC_RESP"
    info "Common causes:"
    info "  503 zoom_sdk_not_available -- set ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET on meeting-api (Pack D pre-flight)."
    info "  Invalid URL / wrong account -- ensure meeting hosted by same account as SDK app."
    exit 1
fi
state_write bot_id "$RECORDER_ID"
pass "recorder: id=$RECORDER_ID"

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

    BOT_NATIVE_ID="$NATIVE_ID" BOT_NAME="$SPEAKER" BOT_PASSCODE="$PASSCODE" \
        BOT_EXTRA='{"voice_agent_enabled": true}' \
        BOT_BODY=$(_make_bot_body)
    BOT_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
        -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
        -d "$BOT_BODY")
    BOT_ID=$(echo "$BOT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

    if [ -n "$BOT_ID" ]; then
        pass "speaker $SPEAKER: user=$USER_ID bot=$BOT_ID"
    else
        fail "speaker $SPEAKER: creation failed: $BOT_RESP"
    fi
done

TOTAL_BOTS=$(( 1 + ${#SPEAKERS[@]} ))
info "$TOTAL_BOTS bots launched (1 recorder + ${#SPEAKERS[@]} speakers)"

echo "  -- phase 3: admit bots --"
echo ""
echo "  $TOTAL_BOTS bots joining Zoom meeting."
echo "  Zoom SDK same-account = auto-admit."
echo "  Polling until all are active..."
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
    info "Common causes:"
    info "  SDK code 63 -- meeting not hosted by same account as SDK app."
    info "  SDK code 12 -- local-recording auto-approve disabled on host Zoom account."
    info "  Inspect bot logs: diagnoseLoadFailure (Pack B) surfaces specific remediation."
    exit 1
fi

echo "  -- phase 4: send TTS --"

SENT=0
for entry in "${GROUND_TRUTH[@]}"; do
    SPEAKER=$(echo "$entry" | cut -d'|' -f1)
    TEXT=$(echo "$entry" | cut -d'|' -f2-)
    TOKEN=${SPEAKER_TOKENS[$SPEAKER]}

    TTS_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
        "$GATEWAY_URL/bots/zoom_sdk/$NATIVE_ID/speak" \
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

echo "  -- phase 5: transcript --"

info "waiting 30s for pipeline..."
sleep 30

RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/transcripts/zoom_sdk/$NATIVE_ID")
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
    fail "transcript: 0 segments -- recorder did not capture audio"
    info "check: was RequestLocalRecordingPrivilege auto-approved on host Zoom account?"
    info "check: did per-user audio flow (onOneWayAudioRawDataReceived)?"
    info "check: transcribe_enabled true on recorder? SDK auth succeeded?"
fi

echo "  -- phase 6: cleanup --"

curl -sf -X DELETE "$GATEWAY_URL/bots/zoom_sdk/$NATIVE_ID" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
for SPEAKER in "${SPEAKERS[@]}"; do
    TOKEN=${SPEAKER_TOKENS[$SPEAKER]}
    curl -sf -X DELETE "$GATEWAY_URL/bots/zoom_sdk/$NATIVE_ID" -H "X-API-Key: $TOKEN" > /dev/null 2>&1 || true
done

pass "cleanup: all bots stopped"

echo "  =============================================="
echo ""
