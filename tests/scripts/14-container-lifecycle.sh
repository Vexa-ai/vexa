#!/usr/bin/env bash
# 14-container-lifecycle.sh — Verify no orphan containers after test run
# Usage: ./14-container-lifecycle.sh GATEWAY_URL API_TOKEN [DEPLOY_MODE]
# Outputs: eval-able LIFECYCLE_OK, ORPHAN_COUNT, ZOMBIE_COUNT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/container-lifecycle"
source "$(dirname "$0")/test-lib.sh"

GATEWAY_URL="${1:?Usage: 14-container-lifecycle.sh GATEWAY_URL API_TOKEN [DEPLOY_MODE]}"
API_TOKEN="${2:?Missing API_TOKEN}"
DEPLOY_MODE="${3:-compose}"

log_start "gateway=$GATEWAY_URL deploy_mode=$DEPLOY_MODE"

FAILED=0
ORPHAN_COUNT=0
ZOMBIE_COUNT=0

# ---------------------------------------------------------------------------
# Step 1: Baseline — record current container state
# ---------------------------------------------------------------------------
echo "--- Step 1: baseline ---" >&2

if [ "$DEPLOY_MODE" = "compose" ]; then
  BASELINE=$(docker ps -a --filter "name=meeting-" --filter "name=agent-" --filter "name=vexa-" \
    --format '{{.Names}} {{.Status}}' 2>/dev/null || echo "")
  BASELINE_COUNT=$(echo "$BASELINE" | grep -c '.' || echo "0")
  log_pass "baseline: $BASELINE_COUNT containers tracked"
elif [ "$DEPLOY_MODE" = "lite" ]; then
  # Find the lite container
  LITE_CONTAINER=$(docker ps --filter "name=vexa" --format '{{.Names}}' | head -1)
  if [ -z "$LITE_CONTAINER" ]; then
    log_fail "no lite container found"
  fi
  BASELINE=$(docker exec "$LITE_CONTAINER" supervisorctl status 2>/dev/null || echo "")
  BASELINE_COUNT=$(echo "$BASELINE" | grep -c '.' || echo "0")
  log_pass "baseline (lite): $BASELINE_COUNT processes in $LITE_CONTAINER"
fi

# ---------------------------------------------------------------------------
# Step 2: Check for orphan/exited containers (compose mode)
# ---------------------------------------------------------------------------
echo "--- Step 2: orphan container check ---" >&2

if [ "$DEPLOY_MODE" = "compose" ]; then
  # Check for exited meeting containers
  EXITED_MEETING=$(docker ps -a --filter "status=exited" --filter "name=meeting-" \
    --format '{{.Names}} {{.Status}}' 2>/dev/null || echo "")

  # Check for exited bot containers (naming varies)
  EXITED_BOT=$(docker ps -a --filter "status=exited" \
    --format '{{.Names}} {{.Status}}' 2>/dev/null | \
    grep -E 'bot|browser|agent' || echo "")

  # Count orphans
  ORPHAN_LINES=""
  [ -n "$EXITED_MEETING" ] && ORPHAN_LINES="$EXITED_MEETING"
  [ -n "$EXITED_BOT" ] && ORPHAN_LINES="${ORPHAN_LINES:+$ORPHAN_LINES\n}$EXITED_BOT"

  if [ -n "$ORPHAN_LINES" ]; then
    ORPHAN_COUNT=$(echo -e "$ORPHAN_LINES" | grep -c '.' || echo "0")
    log_finding "found $ORPHAN_COUNT exited containers:"
    echo -e "$ORPHAN_LINES" | while IFS= read -r line; do
      [ -n "$line" ] && echo "  [orphan] $line" >&2
    done
  else
    ORPHAN_COUNT=0
    log_pass "no orphan exited containers found"
  fi

  # Also check for any containers created by our test that shouldn't exist
  # Look for containers with test-related names
  TEST_CONTAINERS=$(docker ps -a --format '{{.Names}} {{.Status}}' 2>/dev/null | \
    grep -iE 'whk-test|lifecycle-test|test-bot' || echo "")
  if [ -n "$TEST_CONTAINERS" ]; then
    TEST_ORPHANS=$(echo "$TEST_CONTAINERS" | grep -c '.' || echo "0")
    ORPHAN_COUNT=$((ORPHAN_COUNT + TEST_ORPHANS))
    log_finding "found $TEST_ORPHANS test-related orphan containers:"
    echo "$TEST_CONTAINERS" | while IFS= read -r line; do
      echo "  [test-orphan] $line" >&2
    done
  fi

elif [ "$DEPLOY_MODE" = "lite" ]; then
  # In lite mode, check for zombie processes inside the container
  ZOMBIES=$(docker exec "$LITE_CONTAINER" ps aux 2>/dev/null | grep -E '^.*\s+Z\s+' || echo "")
  DEFUNCT=$(docker exec "$LITE_CONTAINER" ps aux 2>/dev/null | grep 'defunct' || echo "")

  ZOMBIE_LINES=""
  [ -n "$ZOMBIES" ] && ZOMBIE_LINES="$ZOMBIES"
  [ -n "$DEFUNCT" ] && ZOMBIE_LINES="${ZOMBIE_LINES:+$ZOMBIE_LINES\n}$DEFUNCT"

  if [ -n "$ZOMBIE_LINES" ]; then
    # Deduplicate
    ZOMBIE_COUNT=$(echo -e "$ZOMBIE_LINES" | sort -u | grep -c '.' || echo "0")
    log_finding "found $ZOMBIE_COUNT zombie/defunct processes in $LITE_CONTAINER (BUG #20):"
    echo -e "$ZOMBIE_LINES" | sort -u | head -10 | while IFS= read -r line; do
      [ -n "$line" ] && echo "  [zombie] $line" >&2
    done
  else
    ZOMBIE_COUNT=0
    log_pass "no zombie processes found in lite container"
  fi
fi

# ---------------------------------------------------------------------------
# Step 3: Verify all bots from the test run reached terminal state via API
# ---------------------------------------------------------------------------
echo "--- Step 3: verify bot terminal states via API ---" >&2

BOTS_RESP=$(curl -sf "$GATEWAY_URL/bots" \
  -H "X-API-Key: $API_TOKEN" 2>/dev/null || echo '{"meetings":[]}')

NON_TERMINAL=$(echo "$BOTS_RESP" | python3 -c "
import sys, json
data = json.load(sys.stdin)
meetings = data.get('meetings', data) if isinstance(data, dict) else data
if isinstance(meetings, dict):
    meetings = meetings.get('meetings', [])
non_terminal = []
for m in meetings:
    status = m.get('status', '')
    if status not in ('completed', 'failed', ''):
        non_terminal.append(f'{m.get(\"id\",\"?\")}: {status} ({m.get(\"platform\",\"?\")})')
if non_terminal:
    for nt in non_terminal:
        print(nt)
else:
    print('ALL_TERMINAL')
" 2>/dev/null || echo "PARSE_ERROR")

if echo "$NON_TERMINAL" | grep -q "ALL_TERMINAL"; then
  log_pass "all bots in terminal state (completed/failed)"
elif echo "$NON_TERMINAL" | grep -q "PARSE_ERROR"; then
  log_finding "could not parse bots response to check terminal states"
else
  NT_COUNT=$(echo "$NON_TERMINAL" | grep -c '.' || echo "0")
  log_finding "$NT_COUNT bots still in non-terminal state:"
  echo "$NON_TERMINAL" | while IFS= read -r line; do
    [ -n "$line" ] && echo "  [non-terminal] $line" >&2
  done
fi

# ---------------------------------------------------------------------------
# Step 4: Check docker ps -a for any containers that should have been removed
# ---------------------------------------------------------------------------
echo "--- Step 4: final container inventory ---" >&2

if [ "$DEPLOY_MODE" = "compose" ]; then
  # Get all vexa-related containers
  ALL_CONTAINERS=$(docker ps -a --format '{{.Names}}\t{{.Status}}' 2>/dev/null | \
    grep -i vexa || echo "")

  RUNNING=$(echo "$ALL_CONTAINERS" | grep -c "Up" || echo "0")
  EXITED_ALL=$(echo "$ALL_CONTAINERS" | grep -c "Exited" || echo "0")

  log_pass "container inventory: $RUNNING running, $EXITED_ALL exited"

  if [ "$EXITED_ALL" -gt 0 ]; then
    ORPHAN_COUNT=$((ORPHAN_COUNT > EXITED_ALL ? ORPHAN_COUNT : EXITED_ALL))
    echo "$ALL_CONTAINERS" | grep "Exited" | while IFS= read -r line; do
      echo "  [exited] $line" >&2
    done
  fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
TOTAL_ISSUES=$((ORPHAN_COUNT + ZOMBIE_COUNT))

echo "ORPHAN_COUNT=$ORPHAN_COUNT"
echo "ZOMBIE_COUNT=$ZOMBIE_COUNT"

if [ "$TOTAL_ISSUES" -gt 0 ]; then
  log_finding "total issues: ORPHANS=$ORPHAN_COUNT ZOMBIES=$ZOMBIE_COUNT"
  echo "LIFECYCLE_OK=false"
  log_pass "container lifecycle check completed with $TOTAL_ISSUES issues found (ORPHANS=$ORPHAN_COUNT, ZOMBIES=$ZOMBIE_COUNT)"
else
  echo "LIFECYCLE_OK=true"
  log_pass "container lifecycle clean: ORPHANS=0 ZOMBIES=0"
fi
