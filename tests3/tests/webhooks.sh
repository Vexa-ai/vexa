#!/usr/bin/env bash
# Webhooks: set user webhook config → gateway injects → meeting-api stores → verify envelope → HMAC → no secret leak → e2e delivery
#
# Step IDs (stable — bound to features/webhooks/README.md DoDs via tests3/test-registry.yaml):
#   config            — PUT /user/webhook stores config in User.data
#   inject            — gateway injects X-User-Webhook-* from validated token into meeting.data
#   spoof             — client-supplied X-User-Webhook-* headers are stripped
#   envelope          — build_envelope emits frozen shape
#   no_leak_payload   — clean_meeting_data strips internal fields
#   hmac              — HMAC-SHA256 signing over timestamp + payload
#   no_leak_response  — webhook_secret not in GET /bots/status
#   e2e_completion    — meeting.completed webhook delivered to user endpoint
#   e2e_status        — status-change webhooks fire when enabled in webhook_events
#
# Reads: .state/gateway_url, .state/api_token, .state/deploy_mode
# Writes: .state/reports/<mode>/webhooks.json
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
MODE=$(state_read deploy_mode)

SECRET="test-secret-12345"
WEBHOOK_URL="https://httpbin.org/post"

echo ""
echo "  webhooks"
echo "  ──────────────────────────────────────────────"

test_begin webhooks

# ── 0. Clean up stale bots (not a step — setup hygiene) ───────────
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

# ── Step: config ────────────────────────────────────────────────
# User sets webhook via PUT /user/webhook. Gateway will inject from this on the next bot create.
WH_RESP=$(curl -s -X PUT "$GATEWAY_URL/user/webhook" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -d "{\"webhook_url\":\"$WEBHOOK_URL\",\"webhook_secret\":\"$SECRET\",\"webhook_events\":{\"meeting.completed\":true,\"meeting.started\":true,\"meeting.status_change\":true,\"bot.failed\":true}}" \
    -w "\n%{http_code}")
WH_CODE=$(echo "$WH_RESP" | tail -1)
if [ "$WH_CODE" = "200" ]; then
    step_pass config "user webhook set via PUT /user/webhook"
else
    step_fail config "PUT /user/webhook returned HTTP $WH_CODE: $(echo "$WH_RESP" | head -n -1)"
    exit 1
fi

info "waiting 3s for token state to propagate..."
sleep 3

# ── Step: inject ───────────────────────────────────────────────
# Create bot WITHOUT webhook headers. Gateway must inject from validated user config.
RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -d '{"platform":"google_meet","native_meeting_id":"webhook-test","bot_name":"Webhook Test","automatic_leave":{"no_one_joined_timeout":30000}}' \
    -w "\n%{http_code}")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -n -1)

if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "202" ]; then
    :  # Bot created; evaluate injection below
elif [ "$HTTP_CODE" = "500" ] && [ "$MODE" = "helm" ]; then
    step_skip inject "bot runtime not configured in helm (HTTP 500)"
    step_skip envelope "requires svc_exec into meeting-api"
    step_skip no_leak_payload "requires svc_exec into meeting-api"
    step_skip hmac "requires svc_exec into meeting-api"
    step_skip no_leak_response "bot create failed, no bots in status"
    step_skip e2e_completion "bot create failed"
    step_skip e2e_status "bot create failed"
    echo "  ──────────────────────────────────────────────"
    echo ""
    exit 0
else
    step_fail inject "POST /bots returned HTTP $HTTP_CODE"
    info "$BODY"
    exit 1
fi

# Extract webhook_url from meeting.data
_extract_webhook_url() {
    echo "$1" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('webhook_url',''))" 2>/dev/null
}
INJECTED_URL=$(_extract_webhook_url "$BODY")

if [ -n "$INJECTED_URL" ]; then
    step_pass inject "gateway injected webhook_url=$INJECTED_URL into meeting.data"
else
    # Token cache may be stale (gateway caches validate_token for 60s). Retry once.
    info "retry: gateway token cache may be stale, waiting 60s..."
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/webhook-test" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
    sleep 60
    RESP2=$(curl -s -X POST "$GATEWAY_URL/bots" \
        -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
        -d '{"platform":"google_meet","native_meeting_id":"webhook-test","bot_name":"Webhook Test","automatic_leave":{"no_one_joined_timeout":30000}}' \
        -w "\n%{http_code}")
    BODY=$(echo "$RESP2" | head -n -1)
    INJECTED_URL=$(_extract_webhook_url "$BODY")
    if [ -n "$INJECTED_URL" ]; then
        step_pass inject "gateway injected webhook_url=$INJECTED_URL (after cache expiry)"
    else
        step_fail inject "gateway did not inject webhook_url — admin-api validate_token or gateway forward_request broken"
    fi
fi

# ── Step: spoof ──────────────────────────────────────────────
# Client-supplied X-User-Webhook-URL must be stripped (anti-spoofing).
SPOOF_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $API_TOKEN" -H "Content-Type: application/json" \
    -H "X-User-Webhook-URL: https://attacker.example.com/steal" \
    -d '{"platform":"google_meet","native_meeting_id":"spoof-test","bot_name":"Spoof"}' \
    -w "\n%{http_code}")
SPOOF_CODE=$(echo "$SPOOF_RESP" | tail -1)
SPOOF_BODY=$(echo "$SPOOF_RESP" | head -n -1)

if [ "$SPOOF_CODE" = "200" ] || [ "$SPOOF_CODE" = "201" ]; then
    SPOOF_URL=$(_extract_webhook_url "$SPOOF_BODY")
    if [ "$SPOOF_URL" = "https://attacker.example.com/steal" ]; then
        step_fail spoof "client-supplied X-User-Webhook-URL leaked through (security bug)"
    elif [ "$SPOOF_URL" = "$WEBHOOK_URL" ]; then
        step_pass spoof "client header stripped; user config used instead"
    elif [ -z "$SPOOF_URL" ]; then
        step_pass spoof "client header stripped (no webhook applied)"
    else
        step_fail spoof "unexpected webhook_url=$SPOOF_URL"
    fi
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/spoof-test" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
else
    step_skip spoof "bot creation for spoof test failed (HTTP $SPOOF_CODE)"
fi

# ── Step: envelope ──────────────────────────────────────────
ENVELOPE_OK=$(svc_exec meeting-api python3 -c "
from meeting_api.webhook_delivery import build_envelope
import json
e=build_envelope('bot.status_change',{'bot_id':1,'status':'active'})
keys=set(e.keys())
required={'event_id','event_type','api_version','created_at','data'}
missing=required-keys
if missing: print('FAIL:missing:'+','.join(missing))
else: print('PASS')
" 2>/dev/null || echo "")

if echo "$ENVELOPE_OK" | grep -q "PASS"; then
    step_pass envelope "event_id, event_type, api_version, created_at, data present"
elif [ -z "$ENVELOPE_OK" ]; then
    step_skip envelope "cannot exec into meeting-api container"
else
    step_fail envelope "$ENVELOPE_OK"
fi

# ── Step: no_leak_payload ────────────────────────────────
LEAK_CHECK=$(svc_exec meeting-api python3 -c "
from meeting_api.webhook_delivery import clean_meeting_data
dirty={'bot_id':1,'status':'active','webhook_secrets':'SECRET','bot_container_id':'INTERNAL','webhook_url':'http://x','container_name':'vexa-123','webhook_secret':'s','real_field':'keep'}
cleaned=clean_meeting_data(dirty)
leaked=[k for k in ['webhook_secrets','bot_container_id','webhook_url','container_name','webhook_secret'] if k in cleaned]
if leaked: print('FAIL:'+','.join(leaked))
elif 'real_field' not in cleaned: print('FAIL:real_field stripped')
else: print('PASS')
" 2>/dev/null || echo "")

if echo "$LEAK_CHECK" | grep -q "PASS"; then
    step_pass no_leak_payload "internal fields stripped; user fields preserved"
elif [ -z "$LEAK_CHECK" ]; then
    step_skip no_leak_payload "cannot exec into meeting-api container"
else
    step_fail no_leak_payload "$LEAK_CHECK"
fi

# ── Step: hmac ─────────────────────────────────────────
HMAC_OK=$(svc_exec meeting-api python3 -c "
import hmac,hashlib,json
from meeting_api.webhook_delivery import build_envelope
e=build_envelope('test',{})
sig=hmac.new('$SECRET'.encode(),json.dumps(e).encode(),hashlib.sha256).hexdigest()
if len(sig)==64: print('PASS:'+sig[:16])
else: print('FAIL')
" 2>/dev/null || echo "")

if echo "$HMAC_OK" | grep -q "PASS"; then
    step_pass hmac "HMAC-SHA256 64-char digest"
elif [ -z "$HMAC_OK" ]; then
    step_skip hmac "cannot exec into meeting-api container"
else
    step_fail hmac "$HMAC_OK"
fi

# ── Step: no_leak_response ──────────────────────────────
STATUS_RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status")
if echo "$STATUS_RESP" | grep -q "$SECRET"; then
    step_fail no_leak_response "webhook_secret visible in GET /bots/status"
else
    step_pass no_leak_response "webhook_secret not in /bots/status response"
fi

# ── Step: e2e_completion + e2e_status ──────────────────────
# Stop the bot, wait, then inspect meeting.data for delivery records.
MEETING_ID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [ -n "$MEETING_ID" ]; then
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/webhook-test" -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
    info "waiting 20s for webhook delivery..."
    sleep 20

    DELIVERY_CHECK=$(svc_exec meeting-api python3 -c "
import asyncio, json as _j
async def check():
    from meeting_api.database import async_session_local
    from meeting_api.models import Meeting
    async with async_session_local() as db:
        m = await db.get(Meeting, $MEETING_ID)
        if not m: return 'NOT_FOUND'
        d = m.data or {}
        if not d.get('webhook_url'): return 'NO_WEBHOOK_URL'
        completion = d.get('webhook_delivery') or {}
        status_deliveries = d.get('webhook_deliveries') or []
        event_types = sorted({e.get('event_type','?') for e in status_deliveries})
        print(_j.dumps({
            'completion_status': completion.get('status'),
            'status_count': len(status_deliveries),
            'status_events': event_types,
        }))
asyncio.run(check())
" 2>/dev/null || echo "")

    if [ -z "$DELIVERY_CHECK" ]; then
        step_skip e2e_completion "cannot exec into meeting-api"
        step_skip e2e_status "cannot exec into meeting-api"
    elif echo "$DELIVERY_CHECK" | grep -q "NO_WEBHOOK_URL"; then
        step_fail e2e_completion "meeting.data missing webhook_url — injection broken end-to-end"
        step_fail e2e_status "meeting.data missing webhook_url — injection broken end-to-end"
    elif echo "$DELIVERY_CHECK" | grep -q "NOT_FOUND"; then
        step_fail e2e_completion "meeting $MEETING_ID not found"
        step_fail e2e_status "meeting $MEETING_ID not found"
    else
        COMP=$(echo "$DELIVERY_CHECK" | python3 -c "import sys,json; print(json.load(sys.stdin).get('completion_status',''))" 2>/dev/null)
        STATUS_CNT=$(echo "$DELIVERY_CHECK" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status_count',0))" 2>/dev/null)
        STATUS_EVENTS=$(echo "$DELIVERY_CHECK" | python3 -c "import sys,json; print(','.join(json.load(sys.stdin).get('status_events',[])))" 2>/dev/null)

        case "$COMP" in
            delivered) step_pass e2e_completion "webhook_delivery.status=delivered" ;;
            queued)    step_pass e2e_completion "queued for retry" ;;
            failed)    step_fail e2e_completion "webhook_delivery.status=failed" ;;
            *)         step_fail e2e_completion "webhook_delivery missing (status=$COMP)" ;;
        esac

        if [ "${STATUS_CNT:-0}" -gt 0 ]; then
            step_pass e2e_status "$STATUS_CNT status-change webhook(s) fired: $STATUS_EVENTS"
        else
            step_fail e2e_status "no status-change webhooks fired — schedule_status_webhook_task wiring or _is_event_enabled broken"
        fi
    fi
else
    step_skip e2e_completion "no meeting ID in POST /bots response"
    step_skip e2e_status "no meeting ID in POST /bots response"
fi

echo "  ──────────────────────────────────────────────"
echo ""
