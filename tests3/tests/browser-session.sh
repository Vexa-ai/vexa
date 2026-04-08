#!/usr/bin/env bash
# Browser session: create → CDP → S3 save → destroy → recreate → verify roundtrip
# Covers DoDs: browser#1-#9,#12-#13, authenticated-meetings#1,#7,#9
# Reads: .state/gateway_url, .state/api_token
# Writes: .state/session_token, .state/browser_container
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)

echo ""
echo "  browser-session"
echo "  ──────────────────────────────────────────────"

# ── 1. Create browser session ─────────────────────
echo "  creating session..."
RESP=$(http_post "$GATEWAY_URL/bots" \
    '{"mode":"browser_session","bot_name":"Browser T1"}' \
    "$API_TOKEN")
SESSION_TOKEN=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('session_token',''))" 2>/dev/null)

if [ -z "$SESSION_TOKEN" ]; then
    fail "create: no session_token"
    info "$RESP"
    exit 1
fi
state_write session_token "$SESSION_TOKEN"
pass "create: session $SESSION_TOKEN"

# ── 2. Wait active ────────────────────────────────
echo "  waiting for active..."
sleep 15
CONTAINER=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    if b.get('data',{}).get('mode')=='browser_session':
        print(b.get('container_name',''))
        break
" 2>/dev/null)

if [ -n "$CONTAINER" ]; then
    state_write browser_container "$CONTAINER"
    pass "active: container $CONTAINER"
else
    fail "session not active after 15s"
    exit 1
fi

# ── 3. CDP proxy reachable ────────────────────────
CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"
CDP_OK=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL',{timeout:10000});
    const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
    await p.goto('https://example.com',{timeout:10000});
    console.log(p.url().includes('example.com')?'OK':'FAIL');
    await b.close();
})().catch(e=>{console.error(e.message);process.exit(1)});
" 2>&1)

if echo "$CDP_OK" | grep -q "OK"; then
    pass "CDP: proxy reachable"
else
    fail "CDP: $CDP_OK"
    exit 1
fi

# ── 4. S3 download on startup ─────────────────────
if pod_logs "$CONTAINER" | grep -q "S3 sync down\|downloading userdata\|syncBrowserData"; then
    pass "S3: download on startup"
else
    info "S3: no download log (first run or no saved data)"
fi

# ── 5. Write marker via localStorage ──────────────
MARKER="test3-$(date +%s)"
WRITE_OK=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL');
    const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
    await p.goto('https://example.com');
    await p.evaluate(m=>localStorage.setItem('vexa_test',m),'$MARKER');
    const v=await p.evaluate(()=>localStorage.getItem('vexa_test'));
    console.log(v==='$MARKER'?'OK':'FAIL:'+v);
    await b.close();
})().catch(e=>{console.error(e.message);process.exit(1)});
" 2>&1)

if echo "$WRITE_OK" | grep -q "OK"; then
    pass "marker: written ($MARKER)"
else
    fail "marker: $WRITE_OK"
    exit 1
fi

# ── 6. Explicit save ──────────────────────────────
SAVE_CODE=$(curl -sf -o /dev/null -w '%{http_code}' -X POST \
    "$GATEWAY_URL/b/$SESSION_TOKEN/save")
if [ "$SAVE_CODE" = "200" ]; then
    pass "save: explicit save → 200"
else
    fail "save: $SAVE_CODE"
fi

# ── 7. Destroy session ───────────────────────────
NATIVE_ID=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    if b.get('data',{}).get('mode')=='browser_session':
        print(b.get('native_meeting_id',''))
        break
" 2>/dev/null)

curl -sf -X DELETE "$GATEWAY_URL/bots/browser_session/$NATIVE_ID" \
    -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
echo "  waiting for cleanup..."
sleep 15

# ── 8. Recreate and read marker ──────────────────
echo "  recreating session..."
RESP2=$(http_post "$GATEWAY_URL/bots" \
    '{"mode":"browser_session","bot_name":"Browser T1b"}' \
    "$API_TOKEN")
SESSION_TOKEN2=$(echo "$RESP2" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('session_token',''))" 2>/dev/null)

if [ -z "$SESSION_TOKEN2" ]; then
    fail "recreate: failed"
    exit 1
fi
state_write session_token "$SESSION_TOKEN2"
sleep 15

CDP_URL2="$GATEWAY_URL/b/$SESSION_TOKEN2/cdp"
READ_OK=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL2',{timeout:10000});
    const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
    await p.goto('https://example.com',{timeout:10000});
    const v=await p.evaluate(()=>localStorage.getItem('vexa_test'));
    if(v==='$MARKER') console.log('ROUNDTRIP_OK');
    else console.log('ROUNDTRIP_FAIL:got='+v);
    await b.close();
})().catch(e=>{console.error(e.message);process.exit(1)});
" 2>&1)

if echo "$READ_OK" | grep -q "ROUNDTRIP_OK"; then
    pass "roundtrip: marker survived destroy→recreate"
else
    fail "roundtrip: data lost ($READ_OK)"
fi

# ── 9. Auth flag check ────────────────────────────
echo "  testing authenticated flag..."
AUTH_RESP=$(http_post "$GATEWAY_URL/bots" \
    '{"platform":"google_meet","native_meeting_id":"auth-flag-test","bot_name":"Auth Flag","authenticated":true,"automatic_leave":{"no_one_joined_timeout":30000}}' \
    "$API_TOKEN")
sleep 5
AUTH_CONTAINER=$(find_bot_pod "")
if [ -n "$AUTH_CONTAINER" ]; then
    HAS_S3=$(pod_exec "$AUTH_CONTAINER" printenv BOT_CONFIG 2>/dev/null | python3 -c "
import sys,json
try:
    c=json.load(sys.stdin)
    print('yes' if c.get('userdataS3Path') else 'no')
except: print('no')
" 2>/dev/null)
    if [ "$HAS_S3" = "yes" ]; then
        pass "auth flag: S3 config in BOT_CONFIG"
    else
        fail "auth flag: no userdataS3Path in BOT_CONFIG"
    fi
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/auth-flag-test" \
        -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
fi

# ── 10. Clean up browser session ──────────────────
NATIVE_ID2=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    if b.get('data',{}).get('mode')=='browser_session':
        print(b.get('native_meeting_id',''))
        break
" 2>/dev/null)
if [ -n "$NATIVE_ID2" ]; then
    curl -sf -X DELETE "$GATEWAY_URL/bots/browser_session/$NATIVE_ID2" \
        -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
fi

echo "  ──────────────────────────────────────────────"
echo ""
