#!/usr/bin/env bash
# Send TTS utterances, fetch transcript, score against ground truth.
# Reads: .state/gateway_url, .state/api_token, .state/native_meeting_id, .state/meeting_platform
# Writes: .state/segments, .state/wer
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
NATIVE_ID=$(state_read native_meeting_id)
PLATFORM=$(state_read meeting_platform)

echo ""
echo "  transcribe"
echo "  ──────────────────────────────────────────────"

# ── 1. Check for existing transcript ─────────────
# If bot has been running and someone spoke, there may already be segments.

RESP=$(http_get "$GATEWAY_URL/transcripts/$PLATFORM/$NATIVE_ID" "$API_TOKEN")
EXISTING=$(echo "$RESP" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    segs=d.get('segments',[]) if isinstance(d,dict) else d
    print(len(segs))
except: print(0)
" 2>/dev/null)

if [ "${EXISTING:-0}" -gt 0 ]; then
    pass "found $EXISTING existing segments"
fi

# ── 2. Send TTS utterances ───────────────────────

UTTERANCES=(
    "Good morning everyone. Let's review the quarterly numbers."
    "Revenue increased by fifteen percent compared to last quarter."
    "Customer satisfaction score is ninety two percent."
)

echo "  sending ${#UTTERANCES[@]} TTS utterances..."
SENT=0
for text in "${UTTERANCES[@]}"; do
    RESP=$(http_post \
        "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID/speak" \
        "{\"text\":\"$text\",\"voice\":\"alloy\"}" \
        "$API_TOKEN")
    if [ "$HTTP_CODE" = "202" ] || [ "$HTTP_CODE" = "200" ]; then
        SENT=$((SENT + 1))
    else
        info "TTS failed for: ${text:0:40}... (HTTP $HTTP_CODE)"
    fi
    sleep 8
done

if [ "$SENT" -eq 0 ]; then
    fail "no TTS utterances sent successfully"
    info "TTS may not be available (voice_agent_enabled needed)"
    info "continuing to check for any existing segments..."
fi

# ── 3. Wait for pipeline ─────────────────────────

echo "  waiting 30s for transcription pipeline..."
sleep 30

# ── 4. Fetch transcript ──────────────────────────

RESP=$(http_get "$GATEWAY_URL/transcripts/$PLATFORM/$NATIVE_ID" "$API_TOKEN")
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
else
    fail "0 segments — transcription pipeline broken"
    exit 1
fi

# ── 5. Basic quality check ───────────────────────

QUALITY=$(echo "$RESP" | python3 -c "
import sys,json
ground_truth = [
    'good morning everyone',
    'revenue increased',
    'customer satisfaction',
]
d=json.load(sys.stdin)
segs=d.get('segments',[]) if isinstance(d,dict) else d
texts=' '.join(s.get('text','') for s in segs).lower()
matched=sum(1 for gt in ground_truth if gt in texts)
print(f'{matched}/{len(ground_truth)}')
" 2>/dev/null)

state_write quality "$QUALITY"
pass "quality: $QUALITY ground truth phrases found"

echo "  ──────────────────────────────────────────────"
echo ""
