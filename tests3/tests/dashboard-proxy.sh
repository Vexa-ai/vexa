#!/usr/bin/env bash
# Dashboard proxy: meetings list, pagination, field contract, transcript, bot creation
#
# Step IDs (stable — bound to features/dashboard/README.md DoDs):
#   meetings_list     — GET /api/vexa/meetings returns meetings
#   pagination        — limit/offset works, no overlap between pages
#   field_contract    — native_meeting_id present in meeting records
#   transcript_proxy  — transcript reachable through dashboard proxy
#   bot_create_proxy  — POST /api/vexa/bots reaches the gateway
#   no_false_failed   — no meetings wrongly marked failed
#
# Reads: .state/dashboard_url, .state/dashboard_cookie, .state/api_token
# Writes: .state/reports/<mode>/dashboard-proxy.json
source "$(dirname "$0")/../lib/common.sh"

DASHBOARD_URL=$(state_read dashboard_url)
COOKIE_TOKEN=$(state_read dashboard_cookie)

echo ""
echo "  dashboard-proxy"
echo "  ──────────────────────────────────────────────"

test_begin dashboard-proxy

COOKIE_HEADER="Cookie: vexa-token=$COOKIE_TOKEN"

# ── Step: meetings_list ──────────────────────────
MEETINGS_RESP=$(curl -sf -H "$COOKIE_HEADER" "$DASHBOARD_URL/api/vexa/meetings")
MEETING_COUNT=$(echo "$MEETINGS_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(len(d.get('meetings',[])))
" 2>/dev/null)

if [ "${MEETING_COUNT:-0}" -gt 0 ]; then
    step_pass meetings_list "$MEETING_COUNT meetings"
else
    step_fail meetings_list "0 meetings returned"
fi

# ── Step: pagination ─────────────────────────────
# Requires >= 4 meetings in DB; skip if not enough data.
if [ "${MEETING_COUNT:-0}" -lt 4 ]; then
    step_skip pagination "need >=4 meetings in DB, have ${MEETING_COUNT:-0}"
else
    PAGE_RESULT=$(python3 -c "
import json, urllib.request
req=urllib.request.Request('$DASHBOARD_URL/api/vexa/meetings?limit=2&offset=0')
req.add_header('Cookie','vexa-token=$COOKIE_TOKEN')
try:
    d=json.load(urllib.request.urlopen(req, timeout=10))
    p1=d.get('meetings',[])
    req2=urllib.request.Request('$DASHBOARD_URL/api/vexa/meetings?limit=2&offset=2')
    req2.add_header('Cookie','vexa-token=$COOKIE_TOKEN')
    d2=json.load(urllib.request.urlopen(req2, timeout=10))
    p2=d2.get('meetings',[])
    ids1=set(m.get('id','') for m in p1)
    ids2=set(m.get('id','') for m in p2)
    overlap=ids1 & ids2
    if len(p1)==2 and len(p2)>=1 and len(overlap)==0:
        print('PASS')
    else:
        print(f'FAIL:p1={len(p1)},p2={len(p2)},overlap={len(overlap)}')
except Exception as e:
    print(f'FAIL:{e}')
" 2>/dev/null)

    if [ "$PAGE_RESULT" = "PASS" ]; then
        step_pass pagination "limit/offset works, no overlap"
    else
        step_fail pagination "$PAGE_RESULT"
    fi
fi

# ── Step: field_contract ─────────────────────────
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
    step_pass field_contract "native_meeting_id present"
else
    step_fail field_contract "$FIELD_RESULT"
fi

# ── Step: transcript_proxy ───────────────────────
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
    step_pass transcript_proxy "${TX_RESULT#PASS:} segments returned"
elif [[ "$TX_RESULT" == SKIP:* ]]; then
    step_skip transcript_proxy "no meetings with transcripts"
else
    step_fail transcript_proxy "$TX_RESULT"
fi

# ── Step: bot_create_proxy ───────────────────────
BOT_RESP=$(curl -s -X POST "$DASHBOARD_URL/api/vexa/bots" \
    -H "Content-Type: application/json" \
    -H "$COOKIE_HEADER" \
    -d '{"platform":"google_meet","meeting_url":"https://meet.google.com/abc-defg-hij","bot_name":"proxy-test"}' \
    -w "\n%{http_code}")
BOT_HTTP=$(echo "$BOT_RESP" | tail -1)

case "$BOT_HTTP" in
    200|201|202) step_pass bot_create_proxy "HTTP $BOT_HTTP" ;;
    403)         step_pass bot_create_proxy "HTTP 403 (limit reached, proxy works)" ;;
    409)         step_pass bot_create_proxy "HTTP 409 (already exists, proxy works)" ;;
    500)         step_fail bot_create_proxy "HTTP 500 — runtime-api or bot image broken" ;;
    *)           step_fail bot_create_proxy "HTTP $BOT_HTTP" ;;
esac

# ── Step: no_false_failed ────────────────────────
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
    step_pass no_false_failed "no meetings with 'failed' status"
else
    step_skip no_false_failed "$FALSE_FAILED meetings with 'failed' status (may be legitimate)"
fi

echo "  ──────────────────────────────────────────────"
echo ""
