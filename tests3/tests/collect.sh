#!/usr/bin/env bash
# Full collection run: create meeting → launch N speaker bots → send TTS →
# capture pipeline output → score → save dataset.
#
# Usage:
#   make -C tests3 collect CONVERSATION=3speakers
#   make -C tests3 collect CONVERSATION=2speakers
#
# Reads:  .state/gateway_url, .state/api_token, .state/admin_url, .state/admin_token
# Reads:  testdata/conversations/conversation-{CONVERSATION}.json
# Writes: testdata/{platform}-{mode}-{date}/ground-truth.json
#         testdata/{platform}-{mode}-{date}/pipeline/rest-segments.json
#         testdata/{platform}-{mode}-{date}/pipeline/score.json
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
ADMIN_URL=$(state_read admin_url)
ADMIN_TOKEN=$(state_read admin_token)
DEPLOY_MODE=$(state_read deploy_mode 2>/dev/null || echo "compose")

T3=$(cd "$(dirname "$0")/.." && pwd)
CONVERSATION=${CONVERSATION:-3speakers}
CONV_FILE="$T3/testdata/conversations/conversation-${CONVERSATION}.json"

echo ""
echo "  collect"
echo "  ══════════════════════════════════════════════"

if [ ! -f "$CONV_FILE" ]; then
    fail "conversation file not found: $CONV_FILE"
    echo "  Available:"
    ls "$T3/testdata/conversations/"*.json 2>/dev/null | while read -r f; do
        name=$(basename "$f" .json | sed 's/conversation-//')
        echo "    $name"
    done
    exit 1
fi

# Parse conversation file
CONV_NAME=$(python3 -c "import json; print(json.load(open('$CONV_FILE'))['name'])" 2>/dev/null)
info "conversation: $CONV_NAME"

# Extract speakers and utterances
SPEAKERS_JSON=$(python3 -c "
import json
c=json.load(open('$CONV_FILE'))
import sys
json.dump(c['speakers'], sys.stdout)
")
UTTERANCES_JSON=$(python3 -c "
import json,sys
c=json.load(open('$CONV_FILE'))
json.dump(c['utterances'], sys.stdout)
")
NUM_UTTERANCES=$(python3 -c "import json; print(len(json.load(open('$CONV_FILE'))['utterances']))")
SPEAKER_IDS=$(python3 -c "
import json
c=json.load(open('$CONV_FILE'))
for s in c['speakers']: print(s['id'])
")

info "$NUM_UTTERANCES utterances, $(echo "$SPEAKER_IDS" | wc -l | tr -d ' ') speakers"

# ── Dataset directory ──
DATE=$(date +%y%m%d)
DATASET_NAME="gmeet-${DEPLOY_MODE}-${DATE}"
DATASET_DIR="$T3/testdata/$DATASET_NAME"
mkdir -p "$DATASET_DIR/pipeline"

# Write ground truth (flat format for scorer)
python3 -c "
import json,sys
c=json.load(open('$CONV_FILE'))
gt=[{'speaker':u['speaker'],'text':u['text'],'delay_ms':u.get('start_after_ms',0)} for u in c['utterances']]
json.dump(gt, sys.stdout, indent=2)
print()
" > "$DATASET_DIR/ground-truth.json"
pass "ground truth: $DATASET_DIR/ground-truth.json"

# ══════════════════════════════════════════════════
#  PHASE 1: Clean stale bots
# ══════════════════════════════════════════════════

info "cleaning stale bots..."
curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    mid=b.get('native_meeting_id','')
    p=b.get('platform','google_meet')
    mode=b.get('data',{}).get('mode','')
    if mode=='browser_session': print(f'browser_session/{mid}')
    else: print(f'{p}/{mid}')
" 2>/dev/null | while read -r bp; do
    curl -sf -X DELETE "$GATEWAY_URL/bots/$bp" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
done
sleep 5

# ══════════════════════════════════════════════════
#  PHASE 2: Create browser session + meeting
# ══════════════════════════════════════════════════

echo "  ── phase 2: create meeting ──────────────────"

info "creating browser session..."
RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -d '{"mode":"browser_session","bot_name":"Collection Host","authenticated":true}')
SESSION_TOKEN=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('session_token',''))" 2>/dev/null)
if [ -z "$SESSION_TOKEN" ]; then
    fail "browser session creation failed: $RESP"
    exit 1
fi
state_write session_token "$SESSION_TOKEN"
pass "browser session: $SESSION_TOKEN"

info "waiting for session active..."
sleep 15
CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"

info "creating meeting via CDP..."
NATIVE_ID=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL',{timeout:15000});
    const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
    await p.goto('https://meet.google.com/new',{timeout:60000,waitUntil:'networkidle'});
    await p.waitForTimeout(3000);
    const m=p.url().match(/meet\\.google\\.com\\/([a-z]+-[a-z]+-[a-z]+)/);
    if(!m){ console.error('FAIL:'+p.url()); process.exit(1); }
    console.log(m[1]);
    const joinBtn=p.locator('button:has-text(\"Join now\")').first();
    if(await joinBtn.isVisible({timeout:5000}).catch(()=>false)){
        await joinBtn.click();
        await p.waitForTimeout(5000);
    }
    b.close();
})().catch(e=>{console.error(e.message);process.exit(1)});
" 2>&1 | head -1)

if [ -z "$NATIVE_ID" ] || echo "$NATIVE_ID" | grep -q "FAIL\|Error"; then
    fail "meeting creation failed: $NATIVE_ID"
    exit 1
fi
state_write native_meeting_id "$NATIVE_ID"
state_write meeting_platform "google_meet"
pass "meeting: $NATIVE_ID"

# ══════════════════════════════════════════════════
#  PHASE 3: Launch recorder + speaker bots
# ══════════════════════════════════════════════════

echo "  ── phase 3: launch bots ─────────────────────"

declare -A SPEAKER_TOKENS
declare -A SPEAKER_VOICES

# Parse speakers from conversation
while IFS= read -r line; do
    SID=$(echo "$line" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
    VOICE=$(echo "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('voice','alloy'))")
    SPEAKER_VOICES[$SID]=$VOICE
done < <(python3 -c "
import json
c=json.load(open('$CONV_FILE'))
for s in c['speakers']: print(json.dumps(s))
")

# Recorder bot (test user)
info "launching recorder..."
REC_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -d "{\"platform\":\"google_meet\",\"native_meeting_id\":\"$NATIVE_ID\",\"bot_name\":\"Recorder\",\"transcribe_enabled\":true,\"automatic_leave\":{\"no_one_joined_timeout\":300000}}")
RECORDER_ID=$(echo "$REC_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [ -z "$RECORDER_ID" ]; then
    fail "recorder creation failed: $REC_RESP"
    exit 1
fi
pass "recorder: id=$RECORDER_ID"

# Speaker bots (one per unique speaker)
SPEAKER_LIST=()
for SID in $(echo "$SPEAKER_IDS"); do
    SPEAKER_LIST+=("$SID")
    SPEAKER_LOWER=$(echo "$SID" | tr '[:upper:]' '[:lower:]')
    SPEAKER_EMAIL="speaker-${SPEAKER_LOWER}@vexa.ai"

    # Find or create user
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
            -d "{\"email\":\"$SPEAKER_EMAIL\",\"name\":\"Speaker $SID\",\"max_concurrent_bots\":3}" 2>/dev/null)
        USER_ID=$(echo "$USER_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
    fi

    TOKEN=$(curl -s -X POST "$ADMIN_URL/admin/users/$USER_ID/tokens?scopes=bot,browser,tx&name=collect-$SPEAKER_LOWER" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)
    SPEAKER_TOKENS[$SID]=$TOKEN

    BOT_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
        -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
        -d "{\"platform\":\"google_meet\",\"native_meeting_id\":\"$NATIVE_ID\",\"bot_name\":\"Speaker $SID\",\"voice_agent_enabled\":true,\"automatic_leave\":{\"no_one_joined_timeout\":300000}}")
    BOT_ID=$(echo "$BOT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

    if [ -n "$BOT_ID" ]; then
        pass "speaker $SID: bot=$BOT_ID voice=${SPEAKER_VOICES[$SID]}"
    else
        fail "speaker $SID: creation failed: $BOT_RESP"
    fi
done

TOTAL_BOTS=$(( 1 + ${#SPEAKER_LIST[@]} ))
info "$TOTAL_BOTS bots launched (1 recorder + ${#SPEAKER_LIST[@]} speakers)"

# ══════════════════════════════════════════════════
#  PHASE 4: Admit ALL bots
# ══════════════════════════════════════════════════

echo "  ── phase 4: admit bots ──────────────────────"
echo ""
echo "  ┌──────────────────────────────────────────────┐"
echo "  │  $TOTAL_BOTS bots waiting. Admit them in the GMeet UI.  │"
echo "  │  Polling until all are active...              │"
echo "  └──────────────────────────────────────────────┘"
echo ""

ALL_TOKENS=("$API_TOKEN")
for SID in "${SPEAKER_LIST[@]}"; do
    ALL_TOKENS+=("${SPEAKER_TOKENS[$SID]}")
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
#  PHASE 5: Send TTS utterances (timed)
# ══════════════════════════════════════════════════

echo "  ── phase 5: send TTS (timed) ────────────────"

START_TIME=$(date +%s%N)
SENT=0
PREV_DELAY=0

python3 -c "
import json,sys
c=json.load(open('$CONV_FILE'))
for u in c['utterances']:
    print(f\"{u['speaker']}|{u.get('start_after_ms',0)}|{u.get('voice', '')}|{u['text']}\")
" | while IFS='|' read -r SPEAKER DELAY_MS VOICE TEXT; do
    # Wait until the right time
    WAIT_MS=$(( DELAY_MS - PREV_DELAY ))
    if [ "$WAIT_MS" -gt 0 ]; then
        WAIT_S=$(python3 -c "print(f'{$WAIT_MS/1000:.1f}')")
        sleep "$WAIT_S"
    fi
    PREV_DELAY=$DELAY_MS

    TOKEN=${SPEAKER_TOKENS[$SPEAKER]}
    USE_VOICE=${VOICE:-${SPEAKER_VOICES[$SPEAKER]:-alloy}}

    TTS_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
        "$GATEWAY_URL/bots/google_meet/$NATIVE_ID/speak" \
        -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
        -d "{\"text\":\"$TEXT\",\"voice\":\"$USE_VOICE\"}" 2>/dev/null || echo "000")

    if [ "$TTS_CODE" = "202" ] || [ "$TTS_CODE" = "200" ]; then
        SENT=$((SENT + 1))
        info "$SPEAKER [${DELAY_MS}ms]: ${TEXT:0:60}..."
    else
        fail "$SPEAKER: TTS failed (HTTP $TTS_CODE)"
    fi
done

pass "TTS: $NUM_UTTERANCES utterances sent with timing"

# ══════════════════════════════════════════════════
#  PHASE 6: Capture pipeline output + score
# ══════════════════════════════════════════════════

echo "  ── phase 6: capture + score ────���────────────"

info "waiting 30s for transcription pipeline..."
sleep 30

# Capture REST segments
RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/transcripts/google_meet/$NATIVE_ID")
echo "$RESP" | python3 -m json.tool > "$DATASET_DIR/pipeline/rest-segments.json" 2>/dev/null || echo "$RESP" > "$DATASET_DIR/pipeline/rest-segments.json"

SEGMENTS=$(echo "$RESP" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    segs=d.get('segments',[]) if isinstance(d,dict) else d
    print(len(segs))
except: print(0)
" 2>/dev/null)

if [ "${SEGMENTS:-0}" -gt 0 ]; then
    pass "captured: $SEGMENTS segments → $DATASET_DIR/pipeline/rest-segments.json"
else
    fail "captured: 0 segments — recorder did not capture audio"
fi

# Score
info "scoring..."
python3 "$T3/lib/score.py" \
    --gt "$DATASET_DIR/ground-truth.json" \
    --segments "$DATASET_DIR/pipeline/rest-segments.json" \
    > "$DATASET_DIR/pipeline/score.json"

pass "score: $DATASET_DIR/pipeline/score.json"

# Print score summary
python3 -c "
import json
s=json.load(open('$DATASET_DIR/pipeline/score.json'))
print(f\"  pass={s['pass']}/{s['gt_count']} missed={s['missed']} hallucinations={s['hallucinations']}\")
print(f\"  speaker={s['speaker_accuracy']:.0%} similarity={s['avg_similarity']:.1%} completeness={s['completeness']:.0%}\")
"

# ══════════════════════════════════════════════════
#  PHASE 7: Cleanup
# ══════════════════════════════════════════════════

echo "  ── phase 7: cleanup ─────────────────────────"

curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/$NATIVE_ID" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
for SID in "${SPEAKER_LIST[@]}"; do
    TOKEN=${SPEAKER_TOKENS[$SID]}
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/$NATIVE_ID" -H "X-API-Key: $TOKEN" > /dev/null 2>&1 || true
done

BROWSER_NATIVE=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    if b.get('data',{}).get('mode')=='browser_session':
        print(b.get('native_meeting_id',''))
        break
" 2>/dev/null)
[ -n "$BROWSER_NATIVE" ] && curl -sf -X DELETE "$GATEWAY_URL/bots/browser_session/$BROWSER_NATIVE" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1

pass "cleanup done"

echo ""
echo "  ══════════════════════════════════════════════"
echo "  Dataset saved: $DATASET_DIR"
echo "  Re-score:      make -C tests3 score DATASET=$DATASET_NAME"
echo "  ══════════════════════════════════════════════"
echo ""
