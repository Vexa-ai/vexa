#!/usr/bin/env bash
# create-live-meeting.sh — Create a Google Meet via authenticated browser session
# Usage: ./create-live-meeting.sh GATEWAY_URL API_TOKEN
# Outputs: eval-able MEETING_URL, MEETING_PLATFORM, SESSION_TOKEN, MEETING_ID
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/create-live-meeting"
source "$(dirname "$0")/test-lib.sh"

GATEWAY_URL="${1:?Usage: create-live-meeting.sh GATEWAY_URL API_TOKEN}"
API_TOKEN="${2:?Missing API_TOKEN}"

log_start "gateway=$GATEWAY_URL"

# 1. Create authenticated browser session via API
SESSION=$(curl -sf -X POST "$GATEWAY_URL/bots" \
  -H "X-API-Key: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"browser_session","bot_name":"meet-creator","authenticated":true}')

SESSION_TOKEN=$(echo "$SESSION" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['session_token'])")
MEETING_ID=$(echo "$SESSION" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
log_pass "browser session created (id=$MEETING_ID, token=${SESSION_TOKEN:0:8}...)"

# 2. Wait for browser to be ready — poll CDP endpoint
CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"
DEADLINE=$(($(date +%s) + 15))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if curl -sf -o /dev/null "$CDP_URL" 2>/dev/null; then
    break
  fi
  sleep 1
done

# 3. Navigate to meet.new via Playwright CDP from host
MEETING_URL=$(node -e "
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.connectOverCDP('$CDP_URL');
  const ctx = browser.contexts()[0];
  const page = ctx.pages()[0] || await ctx.newPage();
  await page.goto('https://meet.new', { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForURL(/meet\.google\.com\/[a-z]{3}-[a-z]{4}-[a-z]{3}/, { timeout: 15000 });
  console.log(page.url().split('?')[0]);
})().catch(e => { console.error('FAIL:', e.message); process.exit(1); });
" 2>/dev/null)

if [ -z "$MEETING_URL" ] || ! echo "$MEETING_URL" | grep -q "meet.google.com"; then
  log_fail "could not create meeting (got: $MEETING_URL)"
fi

log_pass "meeting created at $MEETING_URL"
echo "MEETING_URL=$MEETING_URL"
echo "MEETING_PLATFORM=google_meet"
echo "SESSION_TOKEN=$SESSION_TOKEN"
echo "MEETING_ID=$MEETING_ID"
