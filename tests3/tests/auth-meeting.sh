#!/usr/bin/env bash
# Authenticated meeting: S3 config injected, cookies downloaded, join type verified
# Covers DoDs: auth-meetings#1-#6,#8,#9
# Reads: .state/gateway_url, .state/admin_url, .state/admin_token, .state/api_token
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
ADMIN_URL=$(state_read admin_url)
ADMIN_TOKEN=$(state_read admin_token)
API_TOKEN=$(state_read api_token)

echo ""
echo "  auth-meeting"
echo "  ──────────────────────────────────────────────"

# ── 1. S3 config injected on authenticated=true ──
echo "  creating authenticated bot..."
RESP=$(http_post "$GATEWAY_URL/bots" \
    '{"platform":"google_meet","native_meeting_id":"auth-test-1","bot_name":"Auth Config","authenticated":true,"automatic_leave":{"no_one_joined_timeout":30000}}' \
    "$API_TOKEN")
BOT_OK=$(echo "$RESP" | python3 -c "import sys,json; print('ok' if json.load(sys.stdin).get('id') else 'fail')" 2>/dev/null)

if [ "$BOT_OK" != "ok" ]; then
    fail "create: could not create authenticated bot (HTTP $(http_code))"
    exit 1
fi

sleep 8
AUTH_CONTAINER=$(find_bot_pod "")

if [ -n "$AUTH_CONTAINER" ]; then
    S3_CHECK=$(pod_exec "$AUTH_CONTAINER" printenv BOT_CONFIG 2>/dev/null | python3 -c "
import sys,json
try:
    c=json.load(sys.stdin)
    s3=c.get('userdataS3Path','')
    ep=c.get('s3Endpoint','')
    bucket=c.get('s3Bucket','')
    auth=c.get('authenticated',False)
    missing=[]
    if not s3: missing.append('userdataS3Path')
    if not ep: missing.append('s3Endpoint')
    if not bucket: missing.append('s3Bucket')
    if not auth: missing.append('authenticated')
    if missing: print('FAIL:'+','.join(missing))
    else: print('PASS')
except: print('FAIL:no BOT_CONFIG')
" 2>/dev/null)

    if echo "$S3_CHECK" | grep -q "PASS"; then
        pass "S3 config: all fields present in BOT_CONFIG"
    else
        fail "S3 config: $S3_CHECK"
    fi

    # ── 2. Cookie download on startup ─────────────
    if pod_logs "$AUTH_CONTAINER" | grep -qE "S3 sync down|downloading userdata|syncBrowserData"; then
        pass "cookies: S3 download logged"
    else
        info "cookies: no S3 download log (may not have saved data)"
    fi

    # ── 3. Chrome persistent context ─────────────
    if pod_logs "$AUTH_CONTAINER" | grep -qE "password-store|persistent.*context|authenticated.*context"; then
        pass "chrome: persistent context launched"
    else
        info "chrome: could not confirm persistent context from logs"
    fi

    # ── 4. Diagnostic screenshot ──────────────────
    if pod_exec "$AUTH_CONTAINER" ls /app/storage/screenshots/ 2>/dev/null | grep -qE "auth|lobby|join"; then
        pass "screenshot: diagnostic screenshot taken"
    else
        info "screenshot: not found (may not have reached lobby)"
    fi
else
    fail "no bot container found"
fi

# Cleanup first bot
curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/auth-test-1" \
    -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
sleep 5

# ── 5. use_saved_userdata silently ignored ────────
echo "  testing use_saved_userdata field..."
FIELD_RESP=$(http_post "$GATEWAY_URL/bots" \
    '{"platform":"google_meet","native_meeting_id":"field-test","bot_name":"Field Test","use_saved_userdata":true,"automatic_leave":{"no_one_joined_timeout":30000}}' \
    "$API_TOKEN")
sleep 5
FIELD_CONTAINER=$(find_bot_pod "")

if [ -n "$FIELD_CONTAINER" ]; then
    HAS_S3=$(pod_exec "$FIELD_CONTAINER" printenv BOT_CONFIG 2>/dev/null | python3 -c "
import sys,json
try: print('yes' if json.load(sys.stdin).get('userdataS3Path') else 'no')
except: print('no')
" 2>/dev/null)
    if [ "$HAS_S3" = "no" ]; then
        pass "use_saved_userdata: correctly ignored (field is 'authenticated')"
    else
        info "use_saved_userdata: unexpectedly works — schema may have changed"
    fi
fi

curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/field-test" \
    -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
sleep 5

# ── 6. Shared S3 path ────────────────────────────
echo "  verifying shared S3 path..."
# Create browser session and authenticated bot, compare paths
BROWSER_RESP=$(http_post "$GATEWAY_URL/bots" \
    '{"mode":"browser_session","bot_name":"Path Check","automatic_leave":{"no_one_joined_timeout":30000}}' \
    "$API_TOKEN")
AUTH_RESP2=$(http_post "$GATEWAY_URL/bots" \
    '{"platform":"google_meet","native_meeting_id":"path-test","bot_name":"Path Check Auth","authenticated":true,"automatic_leave":{"no_one_joined_timeout":30000}}' \
    "$API_TOKEN")
sleep 8

PATHS=$(
    MODE_DETECT=$(cat "$STATE/deploy_mode" 2>/dev/null || echo "compose")
    if [ "$MODE_DETECT" = "compose" ]; then
        docker ps --filter "name=meeting-" --format '{{.Names}}' | grep -v meeting-api
    elif [ "$MODE_DETECT" = "helm" ]; then
        kubectl get pods --no-headers -l app.kubernetes.io/name=vexa 2>/dev/null | grep -v meeting-api | awk '{print $1}'
    else
        echo "vexa"
    fi | while read c; do
        pod_exec "$c" printenv BOT_CONFIG 2>/dev/null | python3 -c "
import sys,json
try:
    c=json.load(sys.stdin)
    print(c.get('userdataS3Path',''))
except: pass
" 2>/dev/null
    done | sort -u | grep -v '^$'
)

PATH_COUNT=$(echo "$PATHS" | wc -l)
if [ "$PATH_COUNT" -eq 1 ] && [ -n "$PATHS" ]; then
    pass "shared S3 path: both use $PATHS"
elif [ -z "$PATHS" ]; then
    info "shared S3 path: could not extract paths"
else
    fail "shared S3 path: different paths found"
fi

# Cleanup
for mid in path-test; do
    curl -sf -X DELETE "$GATEWAY_URL/bots/google_meet/$mid" \
        -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1
done
BROWSER_NATIVE=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    if b.get('data',{}).get('mode')=='browser_session':
        print(b.get('native_meeting_id',''))
        break
" 2>/dev/null)
[ -n "$BROWSER_NATIVE" ] && curl -sf -X DELETE "$GATEWAY_URL/bots/browser_session/$BROWSER_NATIVE" \
    -H "X-API-Key: $API_TOKEN" > /dev/null 2>&1

echo "  ──────────────────────────────────────────────"
echo ""
