#!/usr/bin/env bash
# verify-post-meeting.sh — Verify post-meeting deferred transcription with speaker mapping
# Usage: ./verify-post-meeting.sh GATEWAY_URL TOKEN MEETING_PLATFORM NATIVE_MEETING_ID MEETING_ID [LANGUAGE]
# Requires: meeting already completed, recording uploaded to MinIO
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/verify-post-meeting"
source "$(dirname "$0")/test-lib.sh""

GATEWAY_URL="${1:?Usage: verify-post-meeting.sh GATEWAY_URL TOKEN PLATFORM NATIVE_ID MEETING_ID [LANGUAGE]}"
TOKEN="${2:?Missing TOKEN}"
MEETING_PLATFORM="${3:?Missing MEETING_PLATFORM}"
NATIVE_MEETING_ID="${4:?Missing NATIVE_MEETING_ID}"
MEETING_ID="${5:?Missing MEETING_ID (numeric)}"
LANGUAGE="${6:-}"

log_start "meeting=$MEETING_ID platform=$MEETING_PLATFORM native=$NATIVE_MEETING_ID"

# --- Step 1: Verify recording exists ---
RECORDING=$(curl -sf "$GATEWAY_URL/recordings" \
  -H "X-API-Key: $TOKEN" | python3 -c "
import sys, json
for r in json.load(sys.stdin) if isinstance(json.load(open('/dev/stdin')), list) else json.load(sys.stdin).get('recordings', []):
    pass
" 2>/dev/null || echo "")

# Check via meeting data (recordings may be inline)
REC_CHECK=$(curl -sf "$GATEWAY_URL/recordings" -H "X-API-Key: $TOKEN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
recs = d if isinstance(d, list) else d.get('recordings', [])
for r in recs:
    if r.get('meeting_id') == $MEETING_ID and r.get('status') == 'completed':
        mf = r.get('media_files', [])
        print(f'RECORDING_ID={r[\"id\"]} MEDIA_FILES={len(mf)}')
        break
else:
    print('RECORDING_ID= MEDIA_FILES=0')
" 2>/dev/null)
eval "$REC_CHECK"

if [ -z "$RECORDING_ID" ] || [ "$MEDIA_FILES" = "0" ]; then
  log_fail "no completed recording with media files for meeting $MEETING_ID"
fi
log_pass "recording found (id=$RECORDING_ID, $MEDIA_FILES media files)"

# --- Step 2: Trigger deferred transcription ---
BODY="{}"
if [ -n "$LANGUAGE" ]; then
  BODY="{\"language\": \"$LANGUAGE\"}"
fi

RESULT=$(curl -sf -X POST "$GATEWAY_URL/meetings/$MEETING_ID/transcribe" \
  -H "X-API-Key: $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$BODY" 2>/dev/null || echo "")

if [ -z "$RESULT" ]; then
  # Get the error
  ERROR=$(curl -s -X POST "$GATEWAY_URL/meetings/$MEETING_ID/transcribe" \
    -H "X-API-Key: $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$BODY")
  log_fail "POST /meetings/$MEETING_ID/transcribe failed: $ERROR"
fi

SEG_COUNT=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('segment_count', 0))" 2>/dev/null || echo "0")
MESSAGE=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message', ''))" 2>/dev/null || echo "")
log_pass "deferred transcription: $MESSAGE"

# --- Step 3: Verify segments via transcripts API ---
TRANSCRIPT=$(curl -sf "$GATEWAY_URL/transcripts/$MEETING_PLATFORM/$NATIVE_MEETING_ID" \
  -H "X-API-Key: $TOKEN" 2>/dev/null || echo "[]")

# Parse transcript — write vars to temp file to avoid subshell variable loss
_TMP_VARS=$(mktemp)
echo "$TRANSCRIPT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
segs = d if isinstance(d, list) else d.get('segments', [])
realtime = [s for s in segs if not s.get('segment_id','').startswith('deferred:')]
deferred = [s for s in segs if s.get('segment_id','').startswith('deferred:')]
print(f'TOTAL_SEGMENTS={len(segs)}')
print(f'REALTIME_SEGMENTS={len(realtime)}')
print(f'DEFERRED_SEGMENTS={len(deferred)}')

# Check speaker attribution
speakers = set(s.get('speaker') for s in deferred if s.get('speaker'))
has_speakers = len(speakers) > 0
print(f'DEFERRED_SPEAKERS={len(speakers)}')
print(f'SPEAKER_NAMES={\",\".join(speakers)}')
print(f'HAS_SPEAKER_MAPPING={\"true\" if has_speakers else \"false\"}')

# Show segments
for s in deferred[:5]:
    print(f'  [{s.get(\"speaker\",\"None\")}]: {s.get(\"text\",\"\")[:80]}', file=sys.stderr)
" 2>&2 > "$_TMP_VARS"
source "$_TMP_VARS"
rm -f "$_TMP_VARS"

if [ "${DEFERRED_SEGMENTS:-0}" = "0" ]; then
  log_fail "0 deferred segments after transcription"
fi

if [ "${HAS_SPEAKER_MAPPING:-false}" = "true" ]; then
  log_pass "speaker mapping: $DEFERRED_SPEAKERS speakers ($SPEAKER_NAMES)"
else
  log "FINDING" "deferred segments have no speaker mapping (no speaker_events in meeting data)"
fi

log_pass "$DEFERRED_SEGMENTS deferred + $REALTIME_SEGMENTS realtime segments"

echo "POST_MEETING_SEGMENTS=$DEFERRED_SEGMENTS"
echo "RECORDING_UPLOADED=true"
echo "HAS_SPEAKER_MAPPING=${HAS_SPEAKER_MAPPING:-false}"
