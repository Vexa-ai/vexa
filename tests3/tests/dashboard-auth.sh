#!/usr/bin/env bash
# Dashboard auth: login → cookie flags → /me identity → proxy reachable
#
# Step IDs (stable — bound to features/dashboard/README.md DoDs):
#   login             — POST /api/auth/send-magic-link returns 200 + success=true
#   cookie_flags      — vexa-token cookie flags match deployment protocol (Secure iff https)
#   identity          — GET /api/auth/me returns the logged-in user's email
#   proxy_reachable   — GET /api/vexa/meetings via cookie returns 200
#
# Reads: .state/gateway_url, .state/dashboard_url, .state/api_token
# Writes: .state/dashboard_cookie, .state/reports/<mode>/dashboard-auth.json
source "$(dirname "$0")/../lib/common.sh"

DASHBOARD_URL=$(state_read dashboard_url)
API_TOKEN=$(state_read api_token)

echo ""
echo "  dashboard-auth"
echo "  ──────────────────────────────────────────────"

test_begin dashboard-auth

# ── Step: login ──────────────────────────────────
HEADERS_FILE=$(mktemp)
BODY=$(curl -s -D "$HEADERS_FILE" -X POST "$DASHBOARD_URL/api/auth/send-magic-link" \
    -H "Content-Type: application/json" \
    -d '{"email":"test@vexa.ai"}' \
    -c /tmp/tests3-dash-cookies)

LOGIN_OK=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',False))" 2>/dev/null)

if [ "$LOGIN_OK" = "True" ]; then
    step_pass login "200 + success=true"
else
    step_fail login "POST /api/auth/send-magic-link failed: $BODY"
    rm -f "$HEADERS_FILE"
    exit 1
fi

# ── Step: cookie_flags ───────────────────────────
PROTOCOL=$(echo "$DASHBOARD_URL" | grep -o '^https\?')
COOKIE_HEADER=$(grep -i 'set-cookie.*vexa-token' "$HEADERS_FILE" 2>/dev/null | head -1)
rm -f "$HEADERS_FILE"

if [ "$PROTOCOL" = "http" ] && echo "$COOKIE_HEADER" | grep -qi "Secure"; then
    step_fail cookie_flags "Secure flag on HTTP deployment — browser will reject"
    exit 1
else
    step_pass cookie_flags "flags correct for $PROTOCOL"
fi

# ── Step: identity ───────────────────────────────
ME_RESP=$(curl -sf -b /tmp/tests3-dash-cookies "$DASHBOARD_URL/api/auth/me")
ME_EMAIL=$(echo "$ME_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('user',{}).get('email',''))" 2>/dev/null)

if [ "$ME_EMAIL" = "test@vexa.ai" ]; then
    step_pass identity "/me returns test@vexa.ai"
else
    step_fail identity "/me returns '$ME_EMAIL' instead of test@vexa.ai"
    exit 1
fi

# ── Step: proxy_reachable ────────────────────────
PROXY_CODE=$(curl -sf -o /dev/null -w '%{http_code}' -b /tmp/tests3-dash-cookies "$DASHBOARD_URL/api/vexa/meetings")
if [ "$PROXY_CODE" = "200" ]; then
    step_pass proxy_reachable "/api/vexa/meetings → 200"
else
    step_fail proxy_reachable "/api/vexa/meetings → $PROXY_CODE"
fi

# Save cookie token for downstream tests (not a step — plumbing)
COOKIE_TOKEN=$(grep vexa-token /tmp/tests3-dash-cookies 2>/dev/null | awk '{print $NF}')
if [ -n "$COOKIE_TOKEN" ]; then
    state_write dashboard_cookie "$COOKIE_TOKEN"
fi

echo "  ──────────────────────────────────────────────"
echo ""
