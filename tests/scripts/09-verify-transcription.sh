#!/usr/bin/env bash
# verify-transcription.sh — Speaker bot plays audio, listener bot transcribes
# Usage: ./verify-transcription.sh GATEWAY_URL LISTENER_TOKEN SPEAKER_TOKEN MEETING_PLATFORM NATIVE_MEETING_ID SESSION_TOKEN
# Requires: listener bot already active in the meeting, host browser session for admitting speaker
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/verify-transcription"
source "$(dirname "$0")/test-lib.sh"

GATEWAY_URL="${1:?Usage: verify-transcription.sh GATEWAY_URL LISTENER_TOKEN SPEAKER_TOKEN PLATFORM MEETING_ID SESSION_TOKEN}"
LISTENER_TOKEN="${2:?Missing LISTENER_TOKEN (test user)}"
SPEAKER_TOKEN="${3:?Missing SPEAKER_TOKEN (TTS user)}"
MEETING_PLATFORM="${4:?Missing MEETING_PLATFORM}"
NATIVE_MEETING_ID="${5:?Missing NATIVE_MEETING_ID}"
SESSION_TOKEN="${6:?Missing SESSION_TOKEN (for admitting speaker bot)}"

CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"

log_start "meeting=$NATIVE_MEETING_ID"

# --- Step 1: Launch speaker bot ---
SPEAKER=$(curl -sf -X POST "$GATEWAY_URL/bots" \
  -H "X-API-Key: $SPEAKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"platform\": \"$MEETING_PLATFORM\", \"native_meeting_id\": \"$NATIVE_MEETING_ID\", \"bot_name\": \"TTS Speaker\", \"voice_agent_enabled\": true}" 2>/dev/null || echo "")

if [ -z "$SPEAKER" ]; then
  log_fail "could not create speaker bot"
fi
SPEAKER_ID=$(echo "$SPEAKER" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
log_pass "speaker bot created (id=$SPEAKER_ID)"

# --- Step 2: Wait for awaiting_admission, then admit via browser ---
DEADLINE=$(($(date +%s) + 30))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  STATUS=$(curl -sf -H "X-API-Key: $SPEAKER_TOKEN" "$GATEWAY_URL/bots" | python3 -c "
import sys,json
for m in json.load(sys.stdin).get('meetings',[]):
    if m.get('id')==$SPEAKER_ID: print(m['status'])
" 2>/dev/null || echo "unknown")
  [ "$STATUS" = "awaiting_admission" ] && break
  sleep 2
done

if [ "$STATUS" != "awaiting_admission" ]; then
  log_fail "speaker bot did not reach awaiting_admission (status=$STATUS)"
fi

# Admit via browser — open panel, wait for visible, click
node -e "
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.connectOverCDP('$CDP_URL');
  const page = browser.contexts()[0].pages()[0];

  // Click badge to open people panel
  await page.locator('text=/Admit.*guest/').first().click({ timeout: 5000 });
  // Wait for 'Admit all' to become visible in panel
  await page.locator('text=\"Admit all\"').first().waitFor({ state: 'visible', timeout: 5000 });
  await page.locator('text=\"Admit all\"').first().click();
  // Wait for confirmation dialog and click
  await page.locator('button:has-text(\"Admit all\")').last().waitFor({ state: 'visible', timeout: 3000 });
  await page.locator('button:has-text(\"Admit all\")').last().click();
  console.log('admitted');
})().catch(e => { console.error(e.message); process.exit(1); });
" 2>&1 || log_fail "could not admit speaker bot"

# Wait for active
DEADLINE=$(($(date +%s) + 15))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  STATUS=$(curl -sf -H "X-API-Key: $SPEAKER_TOKEN" "$GATEWAY_URL/bots" | python3 -c "
import sys,json
for m in json.load(sys.stdin).get('meetings',[]):
    if m.get('id')==$SPEAKER_ID: print(m['status'])
" 2>/dev/null || echo "unknown")
  [ "$STATUS" = "active" ] && break
  sleep 2
done
[ "$STATUS" != "active" ] && log_fail "speaker bot not active (status=$STATUS)"
log_pass "speaker bot active"

# --- Step 3: Make speaker speak via TTS (proven path) ---
SPEAK=$(curl -sf -X POST "$GATEWAY_URL/bots/$MEETING_PLATFORM/$NATIVE_MEETING_ID/speak" \
  -H "X-API-Key: $SPEAKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, this is a test of the transcription system. Testing one two three."}' 2>/dev/null || echo "")

if [ -z "$SPEAK" ]; then
  log_fail "/speak TTS failed — is tts-service running?"
fi
log_pass "TTS sent via /speak API"

# --- Step 4: Poll listener's transcription ---
DEADLINE=$(($(date +%s) + 30))
POLL=0

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  POLL=$((POLL + 1))
  RESULT=$(curl -sf "$GATEWAY_URL/transcripts/$MEETING_PLATFORM/$NATIVE_MEETING_ID" \
    -H "X-API-Key: $LISTENER_TOKEN" 2>/dev/null || echo '{}')

  COUNT=$(echo "$RESULT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
segs = d.get('segments', d.get('transcript_segments', d.get('data', d if isinstance(d, list) else [])))
print(len(segs))
" 2>/dev/null || echo "0")

  echo "[${POLL}] segments: $COUNT" >&2

  if [ "$COUNT" -gt 0 ]; then
    echo "$RESULT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
segs = d.get('segments', d.get('transcript_segments', d.get('data', d if isinstance(d, list) else [])))
for s in segs[:5]:
    speaker = s.get('speaker', s.get('speaker_name', '?'))
    text = s.get('text', '')[:80]
    print(f'  [{speaker}]: {text}', file=sys.stderr)
print(f'TRANSCRIPT_SEGMENTS={len(segs)}')
" 2>&1
    log_pass "Phase 1 PASS: $COUNT segments from audio playback"

    # Cleanup
    curl -sf -X DELETE "$GATEWAY_URL/bots/$MEETING_PLATFORM/$NATIVE_MEETING_ID" \
      -H "X-API-Key: $SPEAKER_TOKEN" >/dev/null 2>&1
    exit 0
  fi

  sleep 2
done

# Phase 1 failed — diagnose
log "FAIL" "Phase 1: 0 segments after 30s — audio playback → listener pipeline broken"
log "FINDING" "Check speaker bot logs: docker logs <speaker_container> | grep -i 'play\\|paplay\\|ffmpeg\\|error'"

# Cleanup
curl -sf -X DELETE "$GATEWAY_URL/bots/$MEETING_PLATFORM/$NATIVE_MEETING_ID" \
  -H "X-API-Key: $SPEAKER_TOKEN" >/dev/null 2>&1
exit 1
