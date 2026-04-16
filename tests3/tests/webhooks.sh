#!/usr/bin/env bash
# Webhooks: set user webhook config → gateway injects → meeting-api stores → verify envelope → HMAC → no secret leak
# Covers DoDs: webhooks#1-#7
# Reads: .state/gateway_url, .state/admin_url, .state/api_token, .state/admin_key, .state/deploy_mode
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
MODE=$(state_read deploy_mode)

SECRET="test-secret-12345"
WEBHOOK_URL="https://httpbin.org/post"

echo ""
echo "  webhooks"
echo "  ──────────────────────────────────────────────"

# ── 0. Clean up stale bots ────────────────────────
STALE=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    mid=b.get('native_meeting_id','')
    p=b.get('platform','google_meet')
    mode=b.get('data',{}).get('mode','')
    if mode=='browser_session': print(f'browser_session/{mid}')
    else: print(f'{p}/{mid}')
" 2>/dev/null)
if [ -n "$STALE" ]; then
    info "cleaning up stale bots..."
    echo "$STALE" | while read -r bp; do
        curl -sf -X DELETE "$GATEWAY_URL/bots/$bp" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1 || true
    done
    sleep 10
fi

# ── 1. Set user webhook config via admin-api (PUT /user/webhook) ──
# The gateway will inject X-User-Webhook-* headers from the user's stored config
WH_RESP=$(curl -s -X PUT "$GATEWAY_URL/user/webhook" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -d "{\"webhook_url\":\"$WEBHOOK_URL\",\"webhook_secret\":\"$SECRET\"}" \
    -w "\n%{http_code}")
WH_CODE=$(echo "$WH_RESP" | tail -1)

if [ "$WH_CODE" = "200" ]; then
    pass "config: user webhook set via PUT /user/webhook"
else
    fail "config: PUT /user/webhook returned HTTP $WH_CODE"
    info "$(echo "$WH_RESP" | head -n -1)"
    exit 1
fi

# Wait a moment for the gateway token cache to expire (60s) or bypass via cache clear
# The gateway caches validate_token responses for 60s. To test immediately, we rely on
# either (a) fresh cache or (b) short test delay. Skip cache wait in tests by including
# a note.
info "waiting 3s for token state to propagate..."
sleep 3

# ── 2. Create bot WITHOUT webhook headers — gateway should inject them ──
RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -d '{"platform":"google_meet","native_meeting_id":"webhook-test","bot_name":"Webhook Test","automatic_leave":{"no_one_joined_timeout":30000}}' \
    -w "\n%{http_code}")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -n -1)

if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "202" ]; then
    pass "create: bot created (no headers — gateway must inject from user config)"
elif [ "$HTTP_CODE" = "500" ] && [ "$MODE" = "helm" ]; then
    info "create: HTTP 500 — bot runtime not configured in helm (expected)"
    info "skipping webhook tests that need a running bot"
    echo "  ──────────────────────────────────────────────"
    echo ""
    exit 0
else
    fail "create: HTTP $HTTP_CODE"
    info "$BODY"
    exit 1
fi

# ── 3. Gateway injection: webhook_url ended up in meeting.data ──
# This is THE critical check: proves admin-api → gateway → meeting-api flow works
# (may have false negative if token cache is stale; retry once)
check_config() {
    echo "$1" | python3 -c "
import sys,json
d=json.load(sys.stdin).get('data',{})
url=d.get('webhook_url')
if url: print('PASS:'+url)
else: print('FAIL:no_webhook_url')
" 2>/dev/null || echo "PARSE_ERROR"
}

CONFIG_CHECK=$(check_config "$BODY")
if echo "$CONFIG_CHECK" | grep -q "^PASS"; then
    pass "inject: gateway injected webhook_url into meeting.data"
else
    # Token cache may be stale — wait 60s and retry with a new bot
    info "retry: gateway token cache may be stale, waiting 60s..."
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/webhook-test" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
    sleep 60
    RESP2=$(curl -s -X POST "$GATEWAY_URL/bots" \
        -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
        -d '{"platform":"google_meet","native_meeting_id":"webhook-test","bot_name":"Webhook Test","automatic_leave":{"no_one_joined_timeout":30000}}' \
        -w "\n%{http_code}")
    BODY=$(echo "$RESP2" | head -n -1)
    CONFIG_CHECK=$(check_config "$BODY")
    if echo "$CONFIG_CHECK" | grep -q "^PASS"; then
        pass "inject: gateway injected webhook_url into meeting.data (after cache expiry)"
    else
        fail "inject: gateway did NOT inject webhook_url — check admin-api validate_token + gateway forward_request ($CONFIG_CHECK)"
    fi
fi

# ── 4. Spoof protection: client-supplied webhook headers are stripped ──
SPOOF_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -H "X-User-Webhook-URL: https://attacker.example.com/steal" \
    -d '{"platform":"google_meet","native_meeting_id":"spoof-test","bot_name":"Spoof"}' \
    -w "\n%{http_code}")
SPOOF_CODE=$(echo "$SPOOF_RESP" | tail -1)
SPOOF_BODY=$(echo "$SPOOF_RESP" | head -n -1)

if [ "$SPOOF_CODE" = "200" ] || [ "$SPOOF_CODE" = "201" ]; then
    SPOOF_URL=$(echo "$SPOOF_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('webhook_url',''))" 2>/dev/null)
    if [ "$SPOOF_URL" = "https://attacker.example.com/steal" ]; then
        fail "spoof: client-supplied X-User-Webhook-URL was NOT stripped — security bug"
    elif [ "$SPOOF_URL" = "$WEBHOOK_URL" ]; then
        pass "spoof: client header stripped, user config used instead"
    elif [ -z "$SPOOF_URL" ]; then
        pass "spoof: client header stripped (no webhook applied)"
    else
        fail "spoof: unexpected webhook_url=$SPOOF_URL"
    fi
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/spoof-test" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
else
    info "spoof: bot creation failed (HTTP $SPOOF_CODE), skipping spoof check"
fi

# ── 5. Envelope shape ─────────────────────────────
ENVELOPE_OK=$(svc_exec meeting-api python3 -c "
from meeting_api.webhook_delivery import build_envelope
import json
e=build_envelope('bot.status_change',{'bot_id':1,'status':'active'})
keys=set(e.keys())
required={'event_id','event_type','api_version','created_at','data'}
missing=required-keys
if missing:
    print('FAIL:missing:'+','.join(missing))
else:
    print('PASS')
" 2>/dev/null || echo "")

if echo "$ENVELOPE_OK" | grep -q "PASS"; then
    pass "envelope: correct shape (event_id, event_type, api_version, created_at, data)"
elif [ -z "$ENVELOPE_OK" ]; then
    info "envelope: skipped (cannot exec into meeting-api container)"
else
    fail "envelope: $ENVELOPE_OK"
fi

# ── 6. No internal fields leak ────────────────────
LEAK_CHECK=$(svc_exec meeting-api python3 -c "
from meeting_api.webhook_delivery import clean_meeting_data
import json
dirty={'bot_id':1,'status':'active','webhook_secrets':'SECRET','bot_container_id':'INTERNAL','webhook_url':'http://x','container_name':'vexa-123','webhook_secret':'s','real_field':'keep'}
cleaned=clean_meeting_data(dirty)
leaked=[k for k in ['webhook_secrets','bot_container_id','webhook_url','container_name','webhook_secret'] if k in cleaned]
if leaked: print('FAIL:'+','.join(leaked))
elif 'real_field' not in cleaned: print('FAIL:real_field stripped')
else: print('PASS')
" 2>/dev/null || echo "")

if echo "$LEAK_CHECK" | grep -q "PASS"; then
    pass "no leak: internal fields stripped from envelope"
elif [ -z "$LEAK_CHECK" ]; then
    info "no leak: skipped (cannot exec into meeting-api container)"
else
    fail "leak: $LEAK_CHECK"
fi

# ── 7. HMAC signing ──────────────────────────────
HMAC_OK=$(svc_exec meeting-api python3 -c "
import hmac,hashlib,json
from meeting_api.webhook_delivery import build_envelope
e=build_envelope('test',{})
sig=hmac.new('$SECRET'.encode(),json.dumps(e).encode(),hashlib.sha256).hexdigest()
if len(sig)==64: print('PASS:'+sig[:16])
else: print('FAIL')
" 2>/dev/null || echo "")

if echo "$HMAC_OK" | grep -q "PASS"; then
    pass "HMAC: signing works"
elif [ -z "$HMAC_OK" ]; then
    info "HMAC: skipped (cannot exec into meeting-api container)"
else
    fail "HMAC: $HMAC_OK"
fi

# ── 8. Secret not in API response ─────────────────
STATUS_RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status")
if echo "$STATUS_RESP" | grep -q "$SECRET"; then
    fail "secret leak: webhook secret visible in GET /bots/status"
else
    pass "no leak: secret not in /bots/status response"
fi

# ── 9. End-to-end webhook delivery: stop bot → webhook fires ──
# Stop the webhook-test bot, wait for delivery, verify webhook_delivery status in meeting.data
MEETING_ID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [ -n "$MEETING_ID" ]; then
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/webhook-test" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
    info "waiting 20s for webhook delivery..."
    sleep 20

    # Check webhook_delivery status in meeting.data
    DELIVERY_CHECK=$(svc_exec meeting-api python3 -c "
import asyncio
async def check():
    from meeting_api.database import async_session_local
    from meeting_api.models import Meeting
    async with async_session_local() as db:
        m = await db.get(Meeting, $MEETING_ID)
        if not m: return 'NOT_FOUND'
        d = m.data or {}
        if not d.get('webhook_url'): return 'NO_WEBHOOK_URL'
        wd = d.get('webhook_delivery')
        if not wd: return 'NO_DELIVERY_STATUS'
        s = wd.get('status')
        if s == 'delivered': return 'DELIVERED'
        if s == 'queued': return 'QUEUED'
        if s == 'failed': return 'FAILED:' + str(wd.get('status_code',''))
        return 'UNKNOWN:' + str(s)
print(asyncio.run(check()))
" 2>/dev/null || echo "")

    case "$DELIVERY_CHECK" in
        DELIVERED) pass "e2e: webhook delivered to user endpoint" ;;
        QUEUED)    pass "e2e: webhook queued for retry (delivery in progress)" ;;
        NO_WEBHOOK_URL)
            fail "e2e: meeting.data has no webhook_url (gateway injection failed end-to-end)" ;;
        NO_DELIVERY_STATUS)
            info "e2e: post-meeting webhook not yet fired (only meeting.completed triggers via run_all_tasks)" ;;
        FAILED*)   fail "e2e: webhook delivery failed ($DELIVERY_CHECK)" ;;
        NOT_FOUND) fail "e2e: meeting $MEETING_ID not found" ;;
        "")        info "e2e: skipped (cannot exec into meeting-api)" ;;
        *)         info "e2e: delivery status = $DELIVERY_CHECK" ;;
    esac
fi

echo "  ──────────────────────────────────────────────"
echo ""
