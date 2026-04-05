#!/usr/bin/env bash
# 05-browser-session.sh — Create browser session, verify CDP, check Google login
# Usage: ./05-browser-session.sh [API_TOKEN]
# Outputs: eval-able SESSION_TOKEN, CDP_URL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/browser-session"
source "$SCRIPT_DIR/test-lib.sh"

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8056}"

# Load API token
SECRETS_FILE="$SCRIPT_DIR/../secrets/staging.env"
[ -f "$SECRETS_FILE" ] && source "$SECRETS_FILE"
API_TOKEN="${1:-${TEST_API_TOKEN_FULL:-${TEST_API_TOKEN:-}}}"
[ -z "$API_TOKEN" ] && log_fail "no API_TOKEN provided and not in secrets/staging.env"

log_start "gateway=$GATEWAY_URL"

# Step 1: Create browser session
RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
  -H "X-API-Key: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "browser_session", "bot_name": "RT Browser"}' 2>&1)

SESSION_TOKEN=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('session_token',''))" 2>/dev/null)
MEETING_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -z "$SESSION_TOKEN" ]; then
  log_fail "browser session creation failed: $RESP"
fi
log_pass "browser session created id=$MEETING_ID"

# Step 2: Wait for container + CDP
CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"
echo "Waiting for CDP..." >&2
for i in $(seq 1 20); do
  HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$CDP_URL/json/version" 2>/dev/null || echo "000")
  if [ "$HTTP" = "200" ]; then
    break
  fi
  sleep 2
done

if [ "$HTTP" != "200" ]; then
  log_fail "CDP not accessible after 40s: $CDP_URL → $HTTP"
fi
log_pass "CDP accessible at $CDP_URL"

# Step 3: Check Google login by navigating to meet.google.com
LOGIN_CHECK=$(timeout 30 node -e "
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.connectOverCDP('$CDP_URL');
  const p = b.contexts()[0].pages()[0] || await b.contexts()[0].newPage();
  await p.goto('https://meet.google.com', { waitUntil: 'domcontentloaded', timeout: 20000 });
  await p.waitForTimeout(3000);
  const url = p.url();
  if (url.includes('accounts.google.com')) {
    console.log('NOT_LOGGED_IN');
  } else if (url.includes('meet.google.com') || url.includes('workspace.google.com')) {
    console.log('LOGGED_IN');
  } else {
    console.log('UNKNOWN:' + url.substring(0, 80));
  }
  process.exit(0);
})().catch(e => { console.log('ERROR:' + e.message); process.exit(0); });
" 2>/dev/null || echo "TIMEOUT")

if [ "$LOGIN_CHECK" = "LOGGED_IN" ]; then
  log_pass "Google login active (meet.google.com loads)"
elif [ "$LOGIN_CHECK" = "NOT_LOGGED_IN" ]; then
  log "FAIL" "Google login not active — redirected to sign-in. Human must log in via VNC at $GATEWAY_URL/b/$SESSION_TOKEN"
  echo "SESSION_TOKEN=$SESSION_TOKEN"
  echo "CDP_URL=$CDP_URL"
  echo "NEEDS_LOGIN=true"
  exit 1
else
  log "FAIL" "Google login check: $LOGIN_CHECK"
  exit 1
fi

echo "SESSION_TOKEN=$SESSION_TOKEN"
echo "CDP_URL=$CDP_URL"
echo "MEETING_ID=$MEETING_ID"
