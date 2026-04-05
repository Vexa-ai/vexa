#!/usr/bin/env bash
# bot-lifecycle.sh — Launch bot, assert lifecycle stops at awaiting_admission
# Usage: ./bot-lifecycle.sh GATEWAY_URL API_TOKEN MEETING_PLATFORM NATIVE_MEETING_ID [MEETING_URL]
# Outputs: eval-able BOT_ID
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/bot-lifecycle"
source "$(dirname "$0")/test-lib.sh""

GATEWAY_URL="${1:?Usage: bot-lifecycle.sh GATEWAY_URL API_TOKEN PLATFORM MEETING_ID [MEETING_URL]}"
API_TOKEN="${2:?Missing API_TOKEN}"
MEETING_PLATFORM="${3:?Missing MEETING_PLATFORM}"
NATIVE_MEETING_ID="${4:?Missing NATIVE_MEETING_ID}"
MEETING_URL="${5:-}"

# Build request body
BODY="{\"platform\": \"$MEETING_PLATFORM\", \"native_meeting_id\": \"$NATIVE_MEETING_ID\", \"bot_name\": \"Vexa Test Bot\""
if [ -n "$MEETING_URL" ]; then
  BODY="$BODY, \"meeting_url\": \"$MEETING_URL\""
fi
BODY="$BODY}"

# Launch bot
BOT_RESPONSE=$(curl -sf -X POST "$GATEWAY_URL/bots" \
  -H "X-API-Key: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$BODY")

BOT_ID=$(echo "$BOT_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id', d.get('bot_id', '')))")
BOT_STATUS=$(echo "$BOT_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status', d.get('state', '')))")
log_start "bot=$BOT_ID meeting=$NATIVE_MEETING_ID"

# Poll lifecycle — fast polls (2s), short timeout (60s)
DEADLINE=$(($(date +%s) + 60))
SEEN_STATES=""
POLL=0

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  POLL=$((POLL + 1))
  STATUS=$(curl -sf "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('meetings', []):
    if m.get('native_meeting_id') == '$NATIVE_MEETING_ID':
        print(m.get('status', 'unknown'))
        break
else:
    print('unknown')
" 2>/dev/null || echo "unknown")

  if ! echo "$SEEN_STATES" | grep -q "$STATUS"; then
    SEEN_STATES="$SEEN_STATES $STATUS"
    echo "[${POLL}] → $STATUS" >&2
  fi

  case "$STATUS" in
    awaiting_admission)
      log_pass "awaiting_admission (states:$SEEN_STATES)"
      echo "BOT_ID=$BOT_ID"
      exit 0 ;;
    active|ended|completed|failed|error|stopping)
      log_fail "unexpected state '$STATUS' (states:$SEEN_STATES)" ;;
  esac

  sleep 2
done

log_fail "timeout 60s, did not reach awaiting_admission (last: $STATUS, seen:$SEEN_STATES)"
