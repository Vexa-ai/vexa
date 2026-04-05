#!/usr/bin/env bash
# 02-api.sh — API full test: health checks, test user, token
# Usage: ./02-api.sh [GATEWAY_URL] [ADMIN_TOKEN]
# Outputs: eval-able USER_ID, API_TOKEN
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/api-full"
source "$SCRIPT_DIR/test-lib.sh"

GATEWAY_URL="${1:-http://localhost:8056}"
ADMIN_TOKEN="${2:-}"

# Read admin token from container if not provided
if [ -z "$ADMIN_TOKEN" ]; then
  ADMIN_TOKEN=$(docker exec vexa-admin-api-1 printenv ADMIN_API_TOKEN 2>/dev/null || echo "changeme")
fi

log_start "gateway=$GATEWAY_URL admin_token=***${ADMIN_TOKEN: -4}"

FAILED=0

# Step 1: Load or create test user
SECRETS_FILE="$SCRIPT_DIR/../secrets/staging.env"
if [ -f "$SECRETS_FILE" ]; then
  source "$SECRETS_FILE"
fi

# Always ensure token has full scopes (bot,browser,tx)
# The staging.env may have a bot-only token from make setup-api-key
if [ -n "${TEST_API_TOKEN_FULL:-}" ]; then
  TEST_API_TOKEN="$TEST_API_TOKEN_FULL"
fi

if [ -z "${TEST_USER_ID:-}" ] || [ -z "${TEST_API_TOKEN:-}" ]; then
  # Create test user
  USER_RESP=$(curl -sf -X POST "$GATEWAY_URL/admin/users" \
    -H "X-Admin-API-Key: $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"email": "test@vexa.ai", "name": "Test User"}' 2>&1 || echo '{}')
  TEST_USER_ID=$(echo "$USER_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  if [ -z "$TEST_USER_ID" ]; then
    log_fail "cannot create test user: $USER_RESP"
  fi

  # Create token with full scopes
  TOKEN_RESP=$(curl -sf -X POST "$GATEWAY_URL/admin/users/$TEST_USER_ID/tokens?scopes=bot,browser,tx&name=api-test" \
    -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>&1 || echo '{}')
  TEST_API_TOKEN=$(echo "$TOKEN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)
  if [ -z "$TEST_API_TOKEN" ]; then
    log_fail "cannot create API token: $TOKEN_RESP"
  fi
  log_pass "created test user id=$TEST_USER_ID"
else
  log_pass "loaded test user id=$TEST_USER_ID from secrets/staging.env"
fi

# Step 2: Admin API
HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$GATEWAY_URL/admin/users/$TEST_USER_ID" \
  -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null || echo "000")
if [ "$HTTP" = "200" ]; then
  log_pass "admin /users/$TEST_USER_ID → $HTTP"
else
  log "FAIL" "admin /users/$TEST_USER_ID → $HTTP"
  FAILED=$((FAILED + 1))
fi

# Step 3: Meeting API
HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$GATEWAY_URL/meetings" \
  -H "X-API-Key: $TEST_API_TOKEN" 2>/dev/null || echo "000")
if [ "$HTTP" = "200" ]; then
  log_pass "meetings list → $HTTP"
else
  log "FAIL" "meetings list → $HTTP"
  FAILED=$((FAILED + 1))
fi

HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$GATEWAY_URL/bots" \
  -H "X-API-Key: $TEST_API_TOKEN" 2>/dev/null || echo "000")
if [ "$HTTP" = "200" ]; then
  log_pass "bots list → $HTTP"
else
  log "FAIL" "bots list → $HTTP"
  FAILED=$((FAILED + 1))
fi

# Step 4: Runtime API — profiles
PROFILES=$(curl -sf "http://localhost:8090/profiles" \
  -H "X-API-Key: $(docker exec vexa-runtime-api-1 printenv API_KEYS 2>/dev/null | cut -d, -f1)" 2>/dev/null || echo '{}')
HAS_MEETING=$(echo "$PROFILES" | python3 -c "import sys,json; print('yes' if 'meeting' in json.load(sys.stdin) else 'no')" 2>/dev/null)
if [ "$HAS_MEETING" = "yes" ]; then
  log_pass "runtime profiles: meeting profile exists"
else
  log "FAIL" "runtime profiles: no meeting profile"
  FAILED=$((FAILED + 1))
fi

# Step 5: Agent API
HTTP=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:8100/health" 2>/dev/null || echo "000")
if [ "$HTTP" = "200" ]; then
  log_pass "agent-api health → $HTTP"
else
  log "FAIL" "agent-api health → $HTTP"
  FAILED=$((FAILED + 1))
fi

# Step 6: Transcription service
TX_HEALTH=$(curl -sf "http://localhost:8085/health" 2>/dev/null || echo '{}')
TX_GPU=$(echo "$TX_HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('gpu_available','false'))" 2>/dev/null)
if [ "$TX_GPU" = "True" ] || [ "$TX_GPU" = "true" ]; then
  log_pass "transcription: gpu=$TX_GPU"
else
  log "FAIL" "transcription: gpu=$TX_GPU (expected true)"
  FAILED=$((FAILED + 1))
fi

if [ "$FAILED" -gt 0 ]; then
  log_fail "$FAILED checks failed"
fi

log_pass "all $((6)) checks passed"
echo "USER_ID=$TEST_USER_ID"
echo "API_TOKEN=$TEST_API_TOKEN"
