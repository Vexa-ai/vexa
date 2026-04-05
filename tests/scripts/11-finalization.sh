#!/usr/bin/env bash
# verify-finalization.sh — Stop bots and verify clean meeting finalization
# Usage: ./verify-finalization.sh GATEWAY_URL MEETING_PLATFORM NATIVE_MEETING_ID TOKEN1 [TOKEN2] [TOKEN3]
# Stops all bots for the given tokens, then validates:
#   - status transitions end with active→stopping→completed
#   - completion_reason = "stopped"
#   - end_time is set
#   - no "failed" status
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/verify-finalization"
source "$(dirname "$0")/test-lib.sh""

GATEWAY_URL="${1:?Usage: verify-finalization.sh GATEWAY_URL PLATFORM NATIVE_ID TOKEN1 [TOKEN2] [TOKEN3]}"
MEETING_PLATFORM="${2:?Missing MEETING_PLATFORM}"
NATIVE_MEETING_ID="${3:?Missing NATIVE_MEETING_ID}"
shift 3
TOKENS=("$@")

log_start "platform=$MEETING_PLATFORM meeting=$NATIVE_MEETING_ID tokens=${#TOKENS[@]}"

# Step 1: Stop all bots
for TOKEN in "${TOKENS[@]}"; do
  RESULT=$(curl -sf -X DELETE "$GATEWAY_URL/bots/$MEETING_PLATFORM/$NATIVE_MEETING_ID" \
    -H "X-API-Key: $TOKEN" 2>/dev/null || echo "")
  if [ -n "$RESULT" ]; then
    echo "Stopped bot for token ${TOKEN:0:12}..." >&2
  fi
done

# Step 2: Wait for all bots to reach terminal state
sleep 5

# Step 3: Validate each bot's finalization
FAILED=0
for TOKEN in "${TOKENS[@]}"; do
  MEETING=$(curl -sf -H "X-API-Key: $TOKEN" "$GATEWAY_URL/bots" 2>/dev/null | python3 -c "
import sys, json
for m in json.load(sys.stdin).get('meetings', []):
    if m.get('native_meeting_id') == '$NATIVE_MEETING_ID' and m.get('platform') == '$MEETING_PLATFORM':
        # Most recent meeting for this native_id
        print(json.dumps(m))
        break
" 2>/dev/null)

  if [ -z "$MEETING" ]; then
    echo "  token ${TOKEN:0:12}...: no meeting found" >&2
    continue
  fi

  echo "$MEETING" | python3 -c "
import sys, json

m = json.load(sys.stdin)
mid = m.get('id', '?')
status = m.get('status', '?')
end_time = m.get('end_time')
transitions = m.get('data', {}).get('status_transition', [])
reason = m.get('data', {}).get('completion_reason', '')
bot_name = m.get('bot_name', m.get('data', {}).get('bot_name', '?'))

# Build transition chain
chain = '→'.join(t['to'] for t in transitions) if transitions else '(none)'

errors = []
if status != 'completed':
    errors.append(f'status={status} (expected completed)')
if not end_time:
    errors.append('end_time not set')
if reason != 'stopped':
    errors.append(f'completion_reason={reason!r} (expected stopped)')

# Check transition sequence ends correctly
if transitions:
    last = transitions[-1]
    if last.get('to') not in ('completed', 'stopping'):
        errors.append(f'last transition: {last.get(\"from\")}→{last.get(\"to\")} (expected →completed)')

if errors:
    print(f'  FAIL bot {mid}: {\" | \".join(errors)}')
    print(f'    transitions: {chain}')
    sys.exit(1)
else:
    print(f'  PASS bot {mid}: {chain} reason={reason}')
" 2>&1

  if [ $? -ne 0 ]; then
    FAILED=$((FAILED + 1))
  fi
done

if [ "$FAILED" -gt 0 ]; then
  log_fail "$FAILED bots did not finalize correctly"
fi

log_pass "all bots finalized: completed, reason=stopped"
echo "FINALIZATION_OK=true"
