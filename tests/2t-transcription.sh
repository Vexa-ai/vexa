#!/usr/bin/env bash
# transcription-service.sh — Test transcription service with a real audio file
# Usage: ./transcription-service.sh [TRANSCRIPTION_URL] [TRANSCRIPTION_TOKEN]
# Outputs: eval-able TRANSCRIPTION_OK, TRANSCRIPTION_TEXT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/transcription-service"
source "$SCRIPT_DIR/test-lib.sh"

TRANSCRIPTION_URL="${1:-http://localhost:8085}"
TRANSCRIPTION_TOKEN="${2:-}"

# Auto-detect token from worker container if not provided
if [ -z "$TRANSCRIPTION_TOKEN" ]; then
  TRANSCRIPTION_TOKEN=$(docker exec transcription-worker-1 printenv API_TOKEN 2>/dev/null || echo "")
fi

if [ -z "$TRANSCRIPTION_TOKEN" ]; then
  log_fail "no transcription API token (set TRANSCRIPTION_TOKEN or ensure transcription-worker-1 is running)"
fi

log_start "url=$TRANSCRIPTION_URL"

# Step 1: Health check
HEALTH=$(curl -sf "$TRANSCRIPTION_URL/health" 2>/dev/null || echo "")
if [ -z "$HEALTH" ]; then
  log_fail "transcription service not responding at $TRANSCRIPTION_URL/health"
fi

MODEL=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('model','?'))" 2>/dev/null)
GPU=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('gpu_available',False))" 2>/dev/null)
log_pass "health OK (model=$MODEL, gpu=$GPU)"

# Step 2: Transcribe test audio file
TEST_AUDIO="$SCRIPT_DIR/testdata/test-speech-en.wav"
if [ ! -f "$TEST_AUDIO" ]; then
  # Generate if missing (requires espeak-ng)
  if command -v espeak-ng >/dev/null 2>&1; then
    mkdir -p "$SCRIPT_DIR/testdata"
    espeak-ng -w "$TEST_AUDIO" "Hello, this is a test of the transcription system. Testing one two three." 2>/dev/null
    log_pass "generated test audio with espeak-ng"
  else
    log_fail "test audio not found at $TEST_AUDIO and espeak-ng not available"
  fi
fi

RESULT=$(curl -sf -X POST "$TRANSCRIPTION_URL/v1/audio/transcriptions" \
  -H "X-API-Key: $TRANSCRIPTION_TOKEN" \
  -F "file=@$TEST_AUDIO" \
  -F "model=$MODEL" \
  -F "language=en" 2>/dev/null || echo "")

if [ -z "$RESULT" ]; then
  log_fail "transcription request failed (empty response)"
fi

TEXT=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text',''))" 2>/dev/null)
SEGMENTS=$(echo "$RESULT" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('segments',[])))" 2>/dev/null)
DURATION=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('duration',0))" 2>/dev/null)

if [ -z "$TEXT" ] || [ "$SEGMENTS" = "0" ]; then
  log_fail "transcription returned empty (text='$TEXT', segments=$SEGMENTS)"
fi

log_pass "transcribed: '$TEXT' ($SEGMENTS segments, ${DURATION}s)"
echo "TRANSCRIPTION_OK=true"
echo "TRANSCRIPTION_TEXT=$TEXT"
echo "TRANSCRIPTION_SEGMENTS=$SEGMENTS"
