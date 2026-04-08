#!/usr/bin/env bash
# Replay ground-truth transcript as TTS through speaker bots, then score
# the system's transcription output against the original text.
#
# Measures: text accuracy (fuzzy), speaker attribution, latency, persistence.
#
# Usage:
#   MEETING_URL="https://teams.microsoft.com/meet/..." \
#     make -C tests3 transcription-replay
#
# Reads:  .state/gateway_url, .state/api_token, .state/admin_url, .state/admin_token
# Writes: .state/replay-gt.json, .state/replay-output.json, .state/replay-results.json
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
ADMIN_URL=$(state_read admin_url)
ADMIN_TOKEN=$(state_read admin_token)

T3=$(cd "$(dirname "$0")/.." && pwd)
GT_FILE="$T3/meeting_saved_closed_caption.txt"
SCORE_SCRIPT="$T3/lib/replay-score.py"

# ── Validate MEETING_URL ────────────────────────
if [ -z "${MEETING_URL:-}" ]; then
    fail "MEETING_URL not set. Usage: MEETING_URL='https://teams.microsoft.com/meet/...' make -C tests3 transcription-replay"
    exit 1
fi

# Extract native_meeting_id and passcode from URL
NATIVE_ID=$(echo "$MEETING_URL" | grep -oP '/meet/\K\d{10,15}')
PASSCODE=$(echo "$MEETING_URL" | grep -oP '[?&]p=\K[A-Za-z0-9]+')

if [ -z "$NATIVE_ID" ]; then
    fail "Could not extract numeric meeting ID from: $MEETING_URL"
    exit 1
fi
if [ -z "$PASSCODE" ]; then
    fail "Could not extract passcode from: $MEETING_URL"
    exit 1
fi

PLATFORM="teams"

echo ""
echo "  transcription-replay"
echo "  ══════════════════════════════════════════════"
info "URL: $MEETING_URL"
info "native_meeting_id: $NATIVE_ID"
info "passcode: ${PASSCODE:0:4}..."

# ══════════════════════════════════════════════════
#  PHASE 1: Parse ground truth
# ══════════════════════════════════════════════════

echo "  ── phase 1: parse ground truth ────────────────"

python3 -c "
import json, re, sys

gt_file = '$GT_FILE'
win_start = '10:41:21'
win_end   = '10:46:17'

def time_to_secs(t):
    h, m, s = t.split(':')
    return int(h)*3600 + int(m)*60 + int(s)

start_s = time_to_secs(win_start)
end_s   = time_to_secs(win_end)

utterances = []
current_speaker = None
current_time = None

with open(gt_file) as f:
    lines = f.read().strip().split('\n')

i = 0
while i < len(lines):
    line = lines[i].strip()
    m = re.match(r'^\[(.+?)\]\s+(\d{1,2}:\d{2}:\d{2})$', line)
    if m:
        speaker = m.group(1)
        timestamp = m.group(2)
        ts = time_to_secs(timestamp)
        # Collect text lines until next header or blank
        i += 1
        text_lines = []
        while i < len(lines) and lines[i].strip() and not re.match(r'^\[.+?\]\s+\d{1,2}:\d{2}:\d{2}$', lines[i].strip()):
            text_lines.append(lines[i].strip())
            i += 1
        text = ' '.join(text_lines)
        if start_s <= ts <= end_s and text:
            utterances.append({
                'speaker': speaker,
                'timestamp': timestamp,
                'seconds': ts,
                'text': text
            })
    else:
        i += 1

# Compute relative delays (from first utterance)
if utterances:
    base = utterances[0]['seconds']
    for u in utterances:
        u['delay_from_start'] = u['seconds'] - base

# Unique speakers
speakers = sorted(set(u['speaker'] for u in utterances))

gt = {
    'window': {'start': win_start, 'end': win_end},
    'speakers': speakers,
    'utterance_count': len(utterances),
    'utterances': utterances
}

with open('$STATE/replay-gt.json', 'w') as f:
    json.dump(gt, f, indent=2)

print(json.dumps({'speakers': speakers, 'count': len(utterances)}))
" > "$STATE/replay-gt-summary.json"

GT_COUNT=$(python3 -c "import json; print(json.load(open('$STATE/replay-gt.json'))['utterance_count'])")
GT_SPEAKERS=$(python3 -c "import json; print(', '.join(json.load(open('$STATE/replay-gt.json'))['speakers']))")
pass "ground truth: $GT_COUNT utterances"
info "speakers: $GT_SPEAKERS"

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

# Clean speaker bot users too
SPEAKER_EMAILS=$(python3 -c "
import json
gt=json.load(open('$STATE/replay-gt.json'))
for s in gt['speakers']:
    email = s.lower().replace(' ', '_').replace('(', '').replace(')', '')
    print(email)
")

for SPEAKER_SLUG in $SPEAKER_EMAILS; do
    USER_RESP=$(curl -s "$ADMIN_URL/admin/users/email/${SPEAKER_SLUG}@vexa.ai" \
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

    BOTS_TO_CLEAN=$(curl -sf -H "X-API-Key: $TOKEN" "$GATEWAY_URL/bots/status" 2>/dev/null | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    print(b.get('platform','teams')+'/'+b.get('native_meeting_id',''))
" 2>/dev/null || true)
    for bp in $BOTS_TO_CLEAN; do
        curl -sf -X DELETE "$GATEWAY_URL/bots/$bp" -H "X-API-Key: $TOKEN" > /dev/null 2>&1 || true
    done
done

sleep 2
pass "stale bots cleaned"

# ══════════════════════════════════════════════════
#  PHASE 2: Create bots
# ══════════════════════════════════════════════════

echo "  ── phase 2: launch bots ─────────────────────"

state_write native_meeting_id "$NATIVE_ID"
state_write meeting_platform "$PLATFORM"
state_write meeting_url "$MEETING_URL"

# Voice assignments for speakers
declare -A VOICE_MAP
VOICES=(alloy echo fable onyx)
declare -A SPEAKER_TOKENS

SPEAKERS_ARR=()
while IFS= read -r s; do
    SPEAKERS_ARR+=("$s")
done < <(python3 -c "import json; [print(s) for s in json.load(open('$STATE/replay-gt.json'))['speakers']]")

# Assign voices
for i in "${!SPEAKERS_ARR[@]}"; do
    VOICE_MAP["${SPEAKERS_ARR[$i]}"]="${VOICES[$((i % ${#VOICES[@]}))]}"
done

# Recorder bot (test user -- transcribe_enabled)
info "launching recorder..."
REC_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -d "{\"meeting_url\":\"$MEETING_URL\",\"bot_name\":\"Recorder\",\"transcribe_enabled\":true,\"automatic_leave\":{\"no_one_joined_timeout\":600000}}")
RECORDER_ID=$(echo "$REC_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [ -z "$RECORDER_ID" ]; then
    fail "recorder creation failed: $REC_RESP"
    exit 1
fi
state_write bot_id "$RECORDER_ID"
pass "recorder: id=$RECORDER_ID"

# Speaker bots (one per unique speaker, each a separate user)
for SPEAKER in "${SPEAKERS_ARR[@]}"; do
    SPEAKER_SLUG=$(echo "$SPEAKER" | tr '[:upper:]' '[:lower:]' | tr ' ' '_' | tr -d '()')
    SPEAKER_EMAIL="${SPEAKER_SLUG}@vexa.ai"

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

    TOKEN=$(curl -s -X POST "$ADMIN_URL/admin/users/$USER_ID/tokens?scopes=bot,browser,tx&name=replay-$SPEAKER_SLUG" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)

    SPEAKER_TOKENS["$SPEAKER"]=$TOKEN

    BOT_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
        -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
        -d "{\"meeting_url\":\"$MEETING_URL\",\"bot_name\":\"$SPEAKER\",\"voice_agent_enabled\":true,\"automatic_leave\":{\"no_one_joined_timeout\":600000}}")
    BOT_ID=$(echo "$BOT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

    if [ -n "$BOT_ID" ]; then
        pass "speaker $SPEAKER: user=$USER_ID bot=$BOT_ID voice=${VOICE_MAP[$SPEAKER]}"
    else
        fail "speaker $SPEAKER: creation failed: $BOT_RESP"
    fi
done

TOTAL_BOTS=$(( 1 + ${#SPEAKERS_ARR[@]} ))
info "$TOTAL_BOTS bots launched (1 recorder + ${#SPEAKERS_ARR[@]} speakers)"

# ══════════════════════════════════════════════════
#  Wait for admission (human admits from Teams)
# ══════════════════════════════════════════════════

echo "  ── admit bots ───────────────────────────────"
echo ""
echo "  ┌──────────────────────────────────────────────┐"
echo "  |  $TOTAL_BOTS bots waiting in the Teams lobby.          |"
echo "  |  Admit them in the Teams meeting UI.          |"
echo "  |  Polling until all are active...              |"
echo "  └──────────────────────────────────────────────┘"
echo ""

ALL_TOKENS=("$API_TOKEN")
for SPEAKER in "${SPEAKERS_ARR[@]}"; do
    ALL_TOKENS+=("${SPEAKER_TOKENS[$SPEAKER]}")
done

for i in $(seq 1 90); do
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
    fail "only $ACTIVE/$TOTAL_BOTS bots active after polling"
    exit 1
fi

# ══════════════════════════════════════════════════
#  PHASE 3: Replay TTS
# ══════════════════════════════════════════════════

echo "  ── phase 3: replay TTS (~5 min) ──────────────"

SENT=0
TOTAL_UTTERANCES=$GT_COUNT
PREV_DELAY=0

# Clear send log from any previous run
rm -f "$STATE/replay-send-log.jsonl"

# Read utterances as JSON lines for safe handling of special characters
# Use process substitution (not pipe) so the while loop runs in the current shell
# and can access associative arrays (VOICE_MAP, SPEAKER_TOKENS).
while IFS= read -r utt_json; do
    SPEAKER=$(echo "$utt_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['speaker'])")
    TEXT=$(echo "$utt_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['text'])")
    DELAY=$(echo "$utt_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['delay_from_start'])")
    VOICE="${VOICE_MAP[$SPEAKER]:-alloy}"
    TOKEN="${SPEAKER_TOKENS[$SPEAKER]}"

    # Sleep for the gap since the previous utterance
    SLEEP_SECS=$(python3 -c "print(max(0, $DELAY - $PREV_DELAY))")
    if [ "$SLEEP_SECS" != "0" ]; then
        sleep "$SLEEP_SECS"
    fi
    PREV_DELAY=$DELAY

    # Escape text for JSON
    ESCAPED_TEXT=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$TEXT")

    SEND_TS=$(date '+%Y-%m-%dT%H:%M:%S.%3NZ')
    TTS_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
        "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID/speak" \
        -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
        -d "{\"text\":$ESCAPED_TEXT,\"voice\":\"$VOICE\"}" 2>/dev/null || echo "000")

    PREVIEW="${TEXT:0:60}"
    if [ "$TTS_CODE" = "202" ] || [ "$TTS_CODE" = "200" ]; then
        SENT=$((SENT + 1))
        info "[$SENT] $SPEAKER ($VOICE): $PREVIEW"
    else
        fail "$SPEAKER: TTS failed (HTTP $TTS_CODE): $PREVIEW"
    fi

    # Log send event
    echo "$utt_json" | python3 -c "
import sys, json
u = json.load(sys.stdin)
u['send_ts'] = '$SEND_TS'
u['tts_code'] = $TTS_CODE
print(json.dumps(u))
" >> "$STATE/replay-send-log.jsonl"
done < <(python3 -c "
import json
gt = json.load(open('$STATE/replay-gt.json'))
for u in gt['utterances']:
    print(json.dumps(u))
")

# Count sent from log
SENT_COUNT=$(wc -l < "$STATE/replay-send-log.jsonl" 2>/dev/null || echo "0")
SENT_OK=$(python3 -c "
import json
ok=0
with open('$STATE/replay-send-log.jsonl') as f:
    for line in f:
        d=json.loads(line)
        if d.get('tts_code') in (200, 202): ok+=1
print(ok)
" 2>/dev/null || echo "0")

if [ "$SENT_OK" -gt 0 ]; then
    pass "TTS: $SENT_OK/$GT_COUNT utterances sent"
else
    fail "TTS: no utterances sent successfully"
    exit 1
fi

# ══════════════════════════════════════════════════
#  PHASE 4: Drain + collect
# ══════════════════════════════════════════════════

echo "  ── phase 4: drain + collect ───────────────────"

info "waiting 30s for pipeline to process remaining segments..."
sleep 30

RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/transcripts/$PLATFORM/$NATIVE_ID" 2>/dev/null || echo "{}")
echo "$RESP" > "$STATE/replay-output.json"

SEG_COUNT=$(echo "$RESP" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    segs=d.get('segments',[]) if isinstance(d,dict) else d
    print(len(segs))
except: print(0)
" 2>/dev/null)

if [ "${SEG_COUNT:-0}" -gt 0 ]; then
    pass "transcript: $SEG_COUNT segments captured"
else
    fail "transcript: 0 segments -- recorder did not capture audio"
    info "check: are speaker bots' TTS actually playing audio into the meeting?"
    info "check: is the recorder's transcribe_enabled working?"
fi

# ══════════════════════════════════════════════════
#  PHASE 5: Score
# ══════════════════════════════════════════════════

echo "  ── phase 5: score ─────────────────────────────"

SCORE_EXIT=0
python3 "$SCORE_SCRIPT" \
    --gt "$STATE/replay-gt.json" \
    --output "$STATE/replay-output.json" \
    --results "$STATE/replay-results.json" || SCORE_EXIT=$?

if [ -f "$STATE/replay-results.json" ]; then
    COMPLETENESS=$(python3 -c "import json; print(json.load(open('$STATE/replay-results.json'))['completeness'])" 2>/dev/null)
    SPEAKER_ACC=$(python3 -c "import json; print(json.load(open('$STATE/replay-results.json'))['speaker_accuracy'])" 2>/dev/null)
    AVG_SIM=$(python3 -c "import json; print(json.load(open('$STATE/replay-results.json'))['avg_similarity'])" 2>/dev/null)
    SEG_PERSIST=$(python3 -c "import json; print(json.load(open('$STATE/replay-results.json'))['persistence_segments'])" 2>/dev/null)

    info "completeness:     $COMPLETENESS"
    info "speaker_accuracy: $SPEAKER_ACC"
    info "avg_similarity:   $AVG_SIM"
    info "persistence:      $SEG_PERSIST segments"

    if [ "$SCORE_EXIT" -eq 0 ]; then
        pass "scoring thresholds met (completeness >= 0.7, speaker_accuracy >= 0.6)"
    else
        fail "scoring thresholds NOT met"
    fi
else
    fail "scoring script produced no results"
fi

# ══════════════════════════════════════════════════
#  PHASE 6: Cleanup
# ══════════════════════════════════════════════════

echo "  ── phase 6: cleanup ─────────────────────────"

curl -sf -X DELETE "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
for SPEAKER in "${SPEAKERS_ARR[@]}"; do
    TOKEN="${SPEAKER_TOKENS[$SPEAKER]}"
    curl -sf -X DELETE "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID" -H "X-API-Key: $TOKEN" > /dev/null 2>&1 || true
done

pass "cleanup: all bots stopped"

# ── Final report ─────────────────────────────────
echo ""
echo "  ══════════════════════════════════════════════"
echo "  transcription-replay complete"
if [ -f "$STATE/replay-results.json" ]; then
    python3 -c "
import json
r = json.load(open('$STATE/replay-results.json'))
print(f'  completeness={r[\"completeness\"]:.0%}  speaker_accuracy={r[\"speaker_accuracy\"]:.0%}  avg_similarity={r[\"avg_similarity\"]:.1%}')
print(f'  gt={r[\"gt_count\"]}  matched={r[\"matched\"]}  persistence={r[\"persistence_segments\"]} segments')
"
fi
echo "  ══════════════════════════════════════════════"
echo ""

# Clean up temp files
rm -f "$STATE/replay-gt-summary.json" "$STATE/replay-send-log.jsonl"

exit ${SCORE_EXIT:-0}
