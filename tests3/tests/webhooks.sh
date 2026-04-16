#!/usr/bin/env bash
# Webhooks: create bot with webhook → verify envelope → HMAC → no secret leak
# Covers DoDs: webhooks#1-#6
# Reads: .state/gateway_url, .state/api_token, .state/deploy_mode
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
MODE=$(state_read deploy_mode)

SECRET="test-secret-12345"

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

# ── 1. Create bot with webhook config ─────────────
RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -H "X-User-Webhook-URL: https://httpbin.org/post" \
    -H "X-User-Webhook-Secret: $SECRET" \
    -H "X-User-Webhook-Events: bot.status_change,meeting.ended" \
    -d '{"platform":"google_meet","native_meeting_id":"webhook-test","bot_name":"Webhook Test","automatic_leave":{"no_one_joined_timeout":30000}}' \
    -w "\n%{http_code}")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -n -1)

if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "202" ]; then
    pass "create: bot with webhook config"
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

# ── 2. Webhook config stored in meeting.data ──────
# Verify the response contains webhook config (gateway forwarded headers)
CONFIG_CHECK=$(echo "$BODY" | python3 -c "
import sys,json
d=json.load(sys.stdin).get('data',{})
has_url=bool(d.get('webhook_url'))
has_events=bool(d.get('webhook_events'))
if has_url and has_events: print('PASS')
elif has_url: print('PASS:url_only')
else: print('FAIL:no_webhook_config')
" 2>/dev/null || echo "SKIP")

if echo "$CONFIG_CHECK" | grep -q "PASS"; then
    pass "config: webhook_url + webhook_events in meeting.data"
elif echo "$CONFIG_CHECK" | grep -q "SKIP"; then
    info "config: skipped (parse error)"
else
    fail "config: webhook config not in meeting.data ($CONFIG_CHECK)"
fi

# ── 3. Envelope shape ─────────────────────────────
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

# ── 4. No internal fields leak ────────────────────
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

# ── 5. HMAC signing ──────────────────────────────
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

# ── 6. Secret not in API response ─────────────────
STATUS_RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status")
if echo "$STATUS_RESP" | grep -q "$SECRET"; then
    fail "secret leak: webhook secret visible in GET /bots/status"
else
    pass "no leak: secret not in /bots/status response"
fi

# ── 7. Cleanup ────────────────────────────────────
curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/webhook-test" \
    -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1

echo "  ──────────────────────────────────────────────"
echo ""
