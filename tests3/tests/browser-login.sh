#!/usr/bin/env bash
# Browser login: [human] Google login persistence across sessions
# Covers DoDs: browser#10,#11
# Reads: .state/gateway_url, .state/api_token, .state/session_token
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
SESSION_TOKEN=$(state_read session_token)
CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"

echo ""
echo "  browser-login [human gate]"
echo "  ──────────────────────────────────────────────"

# ── 1. Check if already logged into Google ────────
LOGIN_STATE=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL',{timeout:15000});
    const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
    await p.goto('https://myaccount.google.com/',{timeout:15000});
    await p.waitForTimeout(3000);
    const url=p.url();
    if(url.includes('accounts.google.com/signin')||url.includes('accounts.google.com/v3/signin'))
        console.log('LOGIN_REQUIRED');
    else if(url.includes('myaccount.google.com'))
        console.log('LOGGED_IN');
    else console.log('UNKNOWN:'+url);
    await b.close();
})().catch(e=>{console.error(e.message);process.exit(1)});
" 2>&1)

if echo "$LOGIN_STATE" | grep -q "LOGGED_IN"; then
    pass "Google login: already logged in"
else
    echo ""
    echo "  ┌─────────────────────────────────────────────────────┐"
    echo "  │  Google login required.                              │"
    echo "  │  Open the browser session via VNC or CDP and log in. │"
    echo "  │  Then press Enter to continue.                       │"
    echo "  └─────────────────────────────────────────────────────┘"
    echo ""
    read -r -p "  Press Enter after logging in... "

    # Save after login
    curl -sf -X POST "$GATEWAY_URL/b/$SESSION_TOKEN/save" > /dev/null
    pass "Google login: saved to S3"
fi

# ── 2. Verify meet.new works ──────────────────────
echo "  verifying meet.new..."
MEET_OK=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL',{timeout:15000});
    const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
    await p.goto('https://meet.new',{timeout:45000});
    await p.waitForURL('**/meet.google.com/**',{timeout:45000});
    console.log('MEET_OK:'+p.url());
    await b.close();
})().catch(e=>{console.error(e.message);process.exit(1)});
" 2>&1)

if echo "$MEET_OK" | grep -q "MEET_OK"; then
    pass "meet.new: works after login"
else
    fail "meet.new: $MEET_OK"
fi

echo "  ──────────────────────────────────────────────"
echo ""
