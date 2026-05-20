#!/usr/bin/env bash
# dashboard-browser-view — live browser probe for dashboard remote-browser iframe.
#
# This is intentionally stronger than /api/config checks:
#   1. create a browser_session bot via the gateway;
#   2. open the dashboard meeting detail page as that API user;
#   3. click Browser in Playwright;
#   4. assert iframe src uses the browser-facing gateway, not dashboard /b;
#   5. assert no browser CSP block and same-host frame-ancestor is present.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

MODE="$(cat "$STATE/deploy_mode" 2>/dev/null || detect_mode)"
detect_urls "$MODE"

GATEWAY_URL="${GATEWAY_URL:-$(state_read gateway_url)}"
DASHBOARD_URL="${DASHBOARD_URL:-$(state_read dashboard_url)}"
API_TOKEN="${API_TOKEN:-$(state_read api_token)}"
DASHBOARD_COOKIE_NAME="${DASHBOARD_COOKIE_NAME:-$(cat "$STATE/dashboard_cookie_name" 2>/dev/null || true)}"
if [ -z "$DASHBOARD_COOKIE_NAME" ]; then
  case "$MODE" in
    lite) DASHBOARD_COOKIE_NAME="vexa-token-lite" ;;
    compose) DASHBOARD_COOKIE_NAME="vexa-token-compose" ;;
    *) DASHBOARD_COOKIE_NAME="vexa-token" ;;
  esac
fi

test_begin "dashboard-browser-view"

if ! node -e "require('playwright')" >/dev/null 2>&1; then
  step_fail DASHBOARD_BROWSER_VIEW_IFRAME_LOADS "node cannot require playwright; browser view probe cannot run"
  test_end
  exit 0
fi

CHROMIUM_PATH="${PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH:-}"
if [ -z "$CHROMIUM_PATH" ]; then
  CHROMIUM_PATH="$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)"
fi
if [ -n "$CHROMIUM_PATH" ]; then
  export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="$CHROMIUM_PATH"
fi

RUN_SUFFIX="${TEST_RUN_ID:-$(date +%s)-$$}"
BOT_NAME="Dashboard Browser View ${RUN_SUFFIX}"
RESP=$(http_post "$GATEWAY_URL/bots" \
  "$(printf '{"mode":"browser_session","bot_name":"%s"}' "$BOT_NAME")" \
  "$API_TOKEN")
CODE="$(http_code)"
MEETING_ID="$(printf '%s' "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id') or d.get('meeting_id') or d.get('data',{}).get('meeting_id') or '')" 2>/dev/null || true)"
SESSION_TOKEN="$(printf '%s' "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('session_token') or d.get('session_token') or '')" 2>/dev/null || true)"
NATIVE_ID="$(printf '%s' "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('native_meeting_id') or d.get('data',{}).get('native_meeting_id') or '')" 2>/dev/null || true)"

cleanup() {
  if [ -n "${NATIVE_ID:-}" ]; then
    curl -sf -X DELETE "$GATEWAY_URL/bots/browser_session/$NATIVE_ID" \
      -H "X-API-Key: $API_TOKEN" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

if [[ "$CODE" =~ ^20[0-9]$ ]] && [ -n "$MEETING_ID" ]; then
  step_pass DASHBOARD_BROWSER_VIEW_RUNTIME_GATEWAY "created browser_session meeting=$MEETING_ID token=${SESSION_TOKEN:-meeting-id-fallback}"
else
  step_fail DASHBOARD_BROWSER_VIEW_RUNTIME_GATEWAY "POST /bots browser_session failed HTTP $CODE: $(printf '%s' "$RESP" | tr '\n' ' ' | head -c 500)"
  test_end
  exit 0
fi

# Give the browser bot and noVNC server time to publish their Redis session.
sleep "${DASHBOARD_BROWSER_VIEW_BOOT_WAIT:-15}"

if [ -z "$SESSION_TOKEN" ]; then
  SESSION_TOKEN="$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/meetings/$MEETING_ID" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('session_token') or '')" 2>/dev/null || true)"
fi

DASHBOARD_ORIGIN="$(python3 -c 'import sys,urllib.parse as u; p=u.urlparse(sys.argv[1]); print(f"{p.scheme}://{p.netloc}")' "$DASHBOARD_URL")"
BROWSER_TOKEN="${SESSION_TOKEN:-$MEETING_ID}"
HEADERS_FILE="$(mktemp -t dashboard-browser-view-headers-XXXXXX.txt)"
OUT_FILE="$(mktemp -t dashboard-browser-view-XXXXXX.log)"
trap 'cleanup; rm -f "$HEADERS_FILE" "$OUT_FILE"' EXIT INT TERM

CSP_CODE="$(curl -sS -D "$HEADERS_FILE" -o /dev/null -w '%{http_code}' \
  -H "Referer: $DASHBOARD_URL/meetings/$MEETING_ID" \
  "$GATEWAY_URL/b/$BROWSER_TOKEN/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&view_only=false&path=b/$BROWSER_TOKEN/vnc/websockify" || true)"
CSP_LINE="$(tr -d '\r' < "$HEADERS_FILE" | awk 'tolower($0) ~ /^content-security-policy:/ {print; exit}')"
if [ "$CSP_CODE" = "200" ] && printf '%s' "$CSP_LINE" | grep -Fq "$DASHBOARD_ORIGIN"; then
  step_pass DASHBOARD_BROWSER_VIEW_CSP_ALLOWS_SAME_HOST "HTTP 200 and CSP allows $DASHBOARD_ORIGIN"
else
  step_fail DASHBOARD_BROWSER_VIEW_CSP_ALLOWS_SAME_HOST "HTTP $CSP_CODE CSP='$CSP_LINE' expected frame ancestor $DASHBOARD_ORIGIN"
fi

if DASHBOARD_URL="$DASHBOARD_URL" \
  GATEWAY_URL="$GATEWAY_URL" \
  API_TOKEN="$API_TOKEN" \
  DASHBOARD_COOKIE_NAME="$DASHBOARD_COOKIE_NAME" \
  MEETING_ID="$MEETING_ID" \
  SESSION_TOKEN="$SESSION_TOKEN" \
  node "$SCRIPT_DIR/dashboard-browser-view.mjs" >"$OUT_FILE" 2>&1; then
  step_pass DASHBOARD_BROWSER_VIEW_IFRAME_LOADS "$(tr '\n' ' ' < "$OUT_FILE" | head -c 500)"
else
  step_fail DASHBOARD_BROWSER_VIEW_IFRAME_LOADS "$(tr '\n' ' ' < "$OUT_FILE" | head -c 900)"
fi

test_end
