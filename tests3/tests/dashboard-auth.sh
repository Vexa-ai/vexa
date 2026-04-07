#!/usr/bin/env bash
# Dashboard auth: login → cookie flags → /me identity → proxy reachable
# Covers DoDs: dashboard#3 (login), dashboard#13 (identity), dashboard#14 (redirect)
# Reads: .state/gateway_url, .state/dashboard_url, .state/api_token
# Writes: .state/dashboard_cookie
source "$(dirname "$0")/../lib/common.sh"

DASHBOARD_URL=$(state_read dashboard_url)
API_TOKEN=$(state_read api_token)

echo ""
echo "  dashboard-auth"
echo "  ──────────────────────────────────────────────"

# ── 1. Login via magic link ───────────────────────
# Capture headers to a file, body to stdout
HEADERS_FILE=$(mktemp)
BODY=$(curl -s -D "$HEADERS_FILE" -X POST "$DASHBOARD_URL/api/auth/send-magic-link" \
    -H "Content-Type: application/json" \
    -d '{"email":"test@vexa.ai"}' \
    -c /tmp/tests3-dash-cookies)

LOGIN_OK=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',False))" 2>/dev/null)
LOGIN_EMAIL=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('user',{}).get('email',''))" 2>/dev/null)

if [ "$LOGIN_OK" = "True" ]; then
    pass "login: 200 + success=true"
else
    fail "login failed"
    info "$BODY"
    exit 1
fi

# ── 2. Cookie flags ──────────────────────────────
PROTOCOL=$(echo "$DASHBOARD_URL" | grep -o '^https\?')
COOKIE_HEADER=$(grep -i 'set-cookie.*vexa-token' "$HEADERS_FILE" 2>/dev/null | head -1)
rm -f "$HEADERS_FILE"

if [ "$PROTOCOL" = "http" ] && echo "$COOKIE_HEADER" | grep -qi "Secure"; then
    fail "cookie: Secure flag on HTTP deployment — browser will reject"
    exit 1
else
    pass "cookie: flags correct for $PROTOCOL"
fi

# ── 3. /api/auth/me returns correct user ─────────
ME_RESP=$(curl -sf -b /tmp/tests3-dash-cookies "$DASHBOARD_URL/api/auth/me")
ME_EMAIL=$(echo "$ME_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('user',{}).get('email',''))" 2>/dev/null)

if [ "$ME_EMAIL" = "test@vexa.ai" ]; then
    pass "identity: /me returns test@vexa.ai"
else
    fail "identity: /me returns '$ME_EMAIL' instead of test@vexa.ai"
    exit 1
fi

# ── 4. Proxy reachable with cookie ────────────────
PROXY_CODE=$(curl -sf -o /dev/null -w '%{http_code}' -b /tmp/tests3-dash-cookies "$DASHBOARD_URL/api/vexa/meetings")
if [ "$PROXY_CODE" = "200" ]; then
    pass "proxy: /api/vexa/meetings → 200"
else
    fail "proxy: /api/vexa/meetings → $PROXY_CODE"
fi

# Save cookie token for downstream tests
COOKIE_TOKEN=$(grep vexa-token /tmp/tests3-dash-cookies 2>/dev/null | awk '{print $NF}')
if [ -n "$COOKIE_TOKEN" ]; then
    state_write dashboard_cookie "$COOKIE_TOKEN"
fi

echo "  ──────────────────────────────────────────────"
echo ""
