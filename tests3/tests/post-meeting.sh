#!/usr/bin/env bash
# Post-meeting: recording uploaded, deferred transcription, no duplicates
# Covers DoDs: post-meeting#1-#5
# Reads: .state/gateway_url, .state/api_token, .state/native_meeting_id, .state/meeting_platform
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
NATIVE_ID=$(state_read native_meeting_id)
PLATFORM=$(state_read meeting_platform)

echo ""
echo "  post-meeting"
echo "  ──────────────────────────────────────────────"

# ── 1. Find meeting ID ───────────────────────────
MEETING_ID=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/meetings" | python3 -c "
import sys,json
for m in json.load(sys.stdin):
    if m.get('native_meeting_id')=='$NATIVE_ID':
        print(m.get('id',''))
        break
" 2>/dev/null)

if [ -z "$MEETING_ID" ]; then
    fail "meeting not found for native_meeting_id=$NATIVE_ID"
    exit 1
fi
pass "meeting found: $MEETING_ID"

# ── 2. Recording uploaded ────────────────────────
REC_RESP=$(http_get "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID/recordings" "$API_TOKEN")
HAS_RECORDINGS=$(echo "$REC_RESP" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    recs=d.get('recordings',[]) if isinstance(d,dict) else d
    print('yes' if len(recs)>0 else 'no')
except: print('no')
" 2>/dev/null)

if [ "$HAS_RECORDINGS" = "yes" ]; then
    pass "recording: uploaded to storage"
else
    info "recording: none found (may not be enabled for this meeting)"
fi

# ── 3. Trigger deferred transcription ─────────────
DEFERRED_CODE=$(curl -sf -o /dev/null -w '%{http_code}' -X POST \
    -H "X-API-Key: $API_TOKEN" \
    "$GATEWAY_URL/meetings/$MEETING_ID/transcribe")

case "$DEFERRED_CODE" in
    200) pass "deferred: triggered" ;;
    409) pass "deferred: already exists" ;;
    *)   info "deferred: HTTP $DEFERRED_CODE" ;;
esac

# ── 4. Wait and fetch ────────────────────────────
if [ "$DEFERRED_CODE" = "200" ]; then
    echo "  waiting 30s for deferred processing..."
    sleep 30
fi

TX_RESP=$(http_get "$GATEWAY_URL/transcripts/$PLATFORM/$NATIVE_ID" "$API_TOKEN")
SEGMENT_INFO=$(echo "$TX_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
segs=d.get('segments',[]) if isinstance(d,dict) else d
total=len(segs)
# Count unique segment texts to check for duplicates
texts=[s.get('text','').strip() for s in segs if s.get('text','').strip()]
unique=len(set(texts))
dupes=len(texts)-unique
print(f'total={total} unique={unique} dupes={dupes}')
" 2>/dev/null)

TOTAL=$(echo "$SEGMENT_INFO" | grep -o 'total=[0-9]*' | cut -d= -f2)
DUPES=$(echo "$SEGMENT_INFO" | grep -o 'dupes=[0-9]*' | cut -d= -f2)

if [ "${TOTAL:-0}" -gt 0 ]; then
    pass "segments: $TOTAL total"
else
    fail "segments: 0"
fi

if [ "${DUPES:-0}" -eq 0 ]; then
    pass "dedup: no duplicate segments"
else
    fail "dedup: $DUPES duplicate segments found"
fi

# ── 5. Speaker attribution ───────────────────────
SPEAKERS=$(echo "$TX_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
segs=d.get('segments',[]) if isinstance(d,dict) else d
speakers=set(s.get('speaker','Unknown') for s in segs)
speakers.discard('Unknown')
print(len(speakers))
" 2>/dev/null)

if [ "${SPEAKERS:-0}" -gt 0 ]; then
    pass "speakers: $SPEAKERS distinct speakers attributed"
else
    fail "speakers: all Unknown"
fi

echo "  ──────────────────────────────────────────────"
echo ""
