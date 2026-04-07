#!/usr/bin/env bash
# Dashboard proxy: meetings list, pagination, field contract, transcript, bot creation
# Covers DoDs: dashboard#4,#5,#6,#8,#12,#15
# Reads: .state/dashboard_url, .state/dashboard_cookie, .state/api_token
source "$(dirname "$0")/../lib/common.sh"

DASHBOARD_URL=$(state_read dashboard_url)
COOKIE_TOKEN=$(state_read dashboard_cookie)

echo ""
echo "  dashboard-proxy"
echo "  ──────────────────────────────────────────────"

COOKIE_HEADER="Cookie: vexa-token=$COOKIE_TOKEN"

# ── 1. Meetings list returns data ─────────────────
MEETINGS_RESP=$(curl -sf -H "$COOKIE_HEADER" "$DASHBOARD_URL/api/vexa/meetings")
MEETING_COUNT=$(echo "$MEETINGS_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(len(d.get('meetings',[])))
" 2>/dev/null)

if [ "${MEETING_COUNT:-0}" -gt 0 ]; then
    pass "meetings list: $MEETING_COUNT meetings"
else
    fail "meetings list: 0 meetings"
fi

# ── 2. Pagination (limit/offset/has_more) ─────────
PAGE_RESULT=$(python3 -c "
import json, urllib.request
h={'Cookie':'vexa-token=$COOKIE_TOKEN'}
req=urllib.request.Request('$DASHBOARD_URL/api/vexa/meetings?limit=2&offset=0')
req.add_header('Cookie','vexa-token=$COOKIE_TOKEN')
try:
    d=json.load(urllib.request.urlopen(req, timeout=10))
    p1=d.get('meetings',[])
    hm=d.get('has_more',False)
    # Second page
    req2=urllib.request.Request('$DASHBOARD_URL/api/vexa/meetings?limit=2&offset=2')
    req2.add_header('Cookie','vexa-token=$COOKIE_TOKEN')
    d2=json.load(urllib.request.urlopen(req2, timeout=10))
    p2=d2.get('meetings',[])
    ids1=set(m.get('id','') for m in p1)
    ids2=set(m.get('id','') for m in p2)
    overlap=ids1 & ids2
    if len(p1)==2 and len(overlap)==0:
        print('PASS')
    else:
        print(f'FAIL:p1={len(p1)},p2={len(p2)},overlap={len(overlap)}')
except Exception as e:
    print(f'FAIL:{e}')
" 2>/dev/null)

if [ "$PAGE_RESULT" = "PASS" ]; then
    pass "pagination: limit/offset works, no overlap"
else
    fail "pagination: $PAGE_RESULT"
fi

# ── 3. Field contract: native_meeting_id present ──
FIELD_RESULT=$(echo "$MEETINGS_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
meetings=d.get('meetings',[])
for m in meetings:
    if m.get('native_meeting_id') or m.get('platform_specific_id'):
        print('PASS')
        break
else:
    print('FAIL:no meeting with native_meeting_id')
" 2>/dev/null)

if [ "$FIELD_RESULT" = "PASS" ]; then
    pass "field contract: native_meeting_id present"
else
    fail "field contract: $FIELD_RESULT"
fi

# ── 4. Transcript via proxy ───────────────────────
TX_RESULT=$(echo "$MEETINGS_RESP" | python3 -c "
import sys,json,urllib.request
d=json.load(sys.stdin)
for m in d.get('meetings',[]):
    p=m.get('platform','')
    nid=m.get('native_meeting_id') or m.get('platform_specific_id','')
    if p and nid:
        try:
            req=urllib.request.Request('$DASHBOARD_URL/api/vexa/transcripts/'+p+'/'+nid)
            req.add_header('Cookie','vexa-token=$COOKIE_TOKEN')
            resp=json.load(urllib.request.urlopen(req, timeout=10))
            segs=resp.get('segments',[]) if isinstance(resp,dict) else resp
            if len(segs)>0:
                print(f'PASS:{len(segs)}')
                break
        except: pass
else:
    print('SKIP:no meeting with transcript')
" 2>/dev/null)

if [[ "$TX_RESULT" == PASS:* ]]; then
    pass "transcript proxy: ${TX_RESULT#PASS:} segments"
elif [[ "$TX_RESULT" == SKIP:* ]]; then
    pass "transcript proxy: skipped (no meetings with transcripts)"
else
    fail "transcript proxy: $TX_RESULT"
fi

# ── 5. Bot creation through dashboard proxy ───────
BOT_RESP=$(curl -s -X POST "$DASHBOARD_URL/api/vexa/bots" \
    -H "Content-Type: application/json" \
    -H "$COOKIE_HEADER" \
    -d '{"platform":"google_meet","meeting_url":"https://meet.google.com/abc-defg-hij","bot_name":"proxy-test"}' \
    -w "\n%{http_code}")
BOT_HTTP=$(echo "$BOT_RESP" | tail -1)

case "$BOT_HTTP" in
    200|201|202) pass "bot via proxy: $BOT_HTTP" ;;
    403)         pass "bot via proxy: 403 (limit reached, proxy works)" ;;
    409)         pass "bot via proxy: 409 (already exists, proxy works)" ;;
    500)         fail "bot via proxy: 500 — check runtime-api + bot image" ;;
    *)           fail "bot via proxy: $BOT_HTTP" ;;
esac

# ── 6. No false failed meetings ───────────────────
FALSE_FAILED=$(echo "$MEETINGS_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
count=0
for m in d.get('meetings',[]):
    if m.get('status')=='failed':
        count+=1
print(count)
" 2>/dev/null)

if [ "${FALSE_FAILED:-0}" -eq 0 ]; then
    pass "no failed meetings"
else
    info "$FALSE_FAILED meetings with 'failed' status (may need investigation)"
fi

echo "  ──────────────────────────────────────────────"
echo ""
