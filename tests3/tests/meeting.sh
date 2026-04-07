#!/usr/bin/env bash
# Create a live Google Meet meeting via CDP browser session.
# Reads: .state/gateway_url, .state/api_token
# Writes: .state/meeting_url, .state/native_meeting_id, .state/session_token
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)

echo ""
echo "  meeting"
echo "  ──────────────────────────────────────────────"

# ── 1. Create browser session ─────────────────────

echo "  creating browser session..."
RESP=$(http_post "$GATEWAY_URL/bots" \
    '{"mode":"browser_session","bot_name":"Meeting Creator","authenticated":true}' \
    "$API_TOKEN")

SESSION_TOKEN=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('session_token',''))" 2>/dev/null)

if [ -z "$SESSION_TOKEN" ]; then
    fail "could not create browser session"
    info "response: $RESP"
    exit 1
fi

state_write session_token "$SESSION_TOKEN"
pass "browser session created"

# ── 2. Wait for session to be active ──────────────

echo "  waiting for session..."
for i in $(seq 1 12); do
    RESP=$(http_get "$GATEWAY_URL/bots/status" "$API_TOKEN")
    STATUS=$(echo "$RESP" | python3 -c "
import sys,json
bots=json.load(sys.stdin).get('running_bots',[])
for b in bots:
    if b.get('data',{}).get('mode')=='browser_session':
        print(b.get('status',''))
        break
" 2>/dev/null)
    [ "$STATUS" = "running" ] && break
    sleep 5
done

if [ "$STATUS" != "running" ]; then
    fail "browser session not active after 60s (status=$STATUS)"
    exit 1
fi
pass "browser session active"

# ── 3. Navigate to meet.new ───────────────────────

CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"
echo "  creating meeting via meet.new..."

MEET_OUTPUT=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL',{timeout:15000});
    const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
    await p.goto('https://meet.new',{timeout:45000,waitUntil:'domcontentloaded'});
    await p.waitForURL('**/meet.google.com/**',{timeout:45000});
    const url=p.url();
    const m=url.match(/meet\\.google\\.com\\/([a-z]+-[a-z]+-[a-z]+)/);
    if(m) {
        console.log('URL='+url);
        console.log('ID='+m[1]);
    } else {
        console.log('FAIL='+url);
    }
    await b.close();
})().catch(e=>{console.error(e.message);process.exit(1)});
" 2>&1)

MEETING_URL=$(echo "$MEET_OUTPUT" | grep '^URL=' | cut -d= -f2-)
NATIVE_ID=$(echo "$MEET_OUTPUT" | grep '^ID=' | cut -d= -f2-)

if [ -z "$NATIVE_ID" ]; then
    fail "could not create meeting"
    info "$MEET_OUTPUT"
    echo ""
    echo "  If Google login expired, log in via the browser session first."
    exit 1
fi

state_write meeting_url "$MEETING_URL"
state_write native_meeting_id "$NATIVE_ID"
state_write meeting_platform "google_meet"

pass "meeting created: $NATIVE_ID"
echo "  ──────────────────────────────────────────────"
echo ""
