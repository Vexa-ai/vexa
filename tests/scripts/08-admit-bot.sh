#!/usr/bin/env bash
# admit-bot.sh — Admit bot from waiting room via Playwright CDP
# Usage: ./admit-bot.sh GATEWAY_URL API_TOKEN SESSION_TOKEN MEETING_PLATFORM NATIVE_MEETING_ID
# Connects to browser via gateway CDP proxy: $GATEWAY_URL/b/$SESSION_TOKEN/cdp
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/admit-bot"
source "$(dirname "$0")/test-lib.sh"

GATEWAY_URL="${1:?Usage: admit-bot.sh GATEWAY_URL API_TOKEN SESSION_TOKEN PLATFORM MEETING_ID}"
API_TOKEN="${2:?Missing API_TOKEN}"
SESSION_TOKEN="${3:?Missing SESSION_TOKEN}"
MEETING_PLATFORM="${4:?Missing MEETING_PLATFORM}"
NATIVE_MEETING_ID="${5:?Missing NATIVE_MEETING_ID}"

CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"
log_start "cdp=$CDP_URL meeting=$NATIVE_MEETING_ID"

# Click admit button via Playwright CDP from host
node -e "
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.connectOverCDP('$CDP_URL');
  const pages = browser.contexts()[0].pages();
  const page = pages.find(p => p.url().includes('meet.google.com'));
  if (!page) {
    console.error('FAIL: no meeting tab. URLs:', pages.map(p => p.url()));
    process.exit(1);
  }
  console.error('Meeting page:', page.url());

  // Step 1: Click badge by bounding box (text selector unreliable for panel toggle)
  const badge = page.locator('text=/Admit \\\\d+ guest/').first();
  const box = await badge.boundingBox();
  if (!box) {
    await page.screenshot({ path: '/tmp/admit-fail.png' });
    console.error('FAIL: no Admit badge found');
    process.exit(1);
  }
  await page.mouse.click(box.x + box.width/2, box.y + box.height/2);
  console.error('Badge clicked at', JSON.stringify(box));
  await new Promise(r => setTimeout(r, 2000));

  // Step 2: Click 'Admit all' in people panel (now visible)
  await page.locator('text=\"Admit all\"').first().waitFor({ state: 'visible', timeout: 5000 });
  await page.locator('text=\"Admit all\"').first().click();
  console.error('Clicked Admit all');

  // Step 3: Confirm dialog
  await new Promise(r => setTimeout(r, 500));
  await page.locator('button:has-text(\"Admit all\")').last().waitFor({ state: 'visible', timeout: 3000 });
  await page.locator('button:has-text(\"Admit all\")').last().click();
  console.error('Confirmed admit dialog');
})().catch(e => { console.error('FAIL:', e.message); process.exit(1); });
" 2>&1

if [ $? -ne 0 ]; then
  log_fail "could not admit bot"
fi

# Poll for bot → active — 2s intervals, 30s timeout
DEADLINE=$(($(date +%s) + 30))
POLL=0

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  POLL=$((POLL + 1))
  STATUS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for m in d.get('meetings', []):
    if m.get('native_meeting_id') == '$NATIVE_MEETING_ID':
        print(m.get('status', 'unknown'))
        break
else:
    print('unknown')
" 2>/dev/null || echo "unknown")

  echo "[${POLL}] Bot status: $STATUS" >&2
  if [ "$STATUS" = "active" ]; then
    log_pass "bot active after admission"
    echo "BOT_ADMITTED=true"
    exit 0
  fi
  sleep 2
done

log_fail "bot did not become active after 30s (status=$STATUS)"
