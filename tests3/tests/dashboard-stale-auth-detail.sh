#!/usr/bin/env bash
# dashboard-stale-auth-detail — stale auth must not render raw Invalid API key.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

MODE="$(cat "$STATE/deploy_mode" 2>/dev/null || detect_mode)"
DASHBOARD_URL="${DASHBOARD_URL:-$(cat "$STATE/dashboard_url" 2>/dev/null || true)}"
if [ -z "$DASHBOARD_URL" ]; then
  case "$MODE" in
    lite) DASHBOARD_URL="http://localhost:3100" ;;
    compose) DASHBOARD_URL="http://localhost:3001" ;;
    *) DASHBOARD_URL="http://localhost:3100" ;;
  esac
fi

case "$DASHBOARD_URL" in
  *:3100*) DASHBOARD_AUTH_COOKIE_NAME="${DASHBOARD_AUTH_COOKIE_NAME:-vexa-token-lite}" ;;
  *:3001*) DASHBOARD_AUTH_COOKIE_NAME="${DASHBOARD_AUTH_COOKIE_NAME:-vexa-token-compose}" ;;
  *) DASHBOARD_AUTH_COOKIE_NAME="${DASHBOARD_AUTH_COOKIE_NAME:-vexa-token}" ;;
esac
export DASHBOARD_AUTH_COOKIE_NAME

test_begin "dashboard-stale-auth-detail"

if ! node -e "require('playwright')" >/dev/null 2>&1; then
  step_skip DASHBOARD_DETAIL_STALE_AUTH_RECOVERS "playwright unavailable in this env (e.g. headless VM) — stale-auth probe skipped; covered locally"
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

OUT_FILE="$(mktemp -t dashboard-stale-auth-detail-XXXXXX.log)"
trap 'rm -f "$OUT_FILE"; _flush_test_report' EXIT INT TERM

if DASHBOARD_URL="$DASHBOARD_URL" node "$SCRIPT_DIR/dashboard-stale-auth-detail.mjs" >"$OUT_FILE" 2>&1; then
  step_pass DASHBOARD_DETAIL_STALE_AUTH_RECOVERS "$(tr '\n' ' ' < "$OUT_FILE" | head -c 400)"
else
  step_fail DASHBOARD_DETAIL_STALE_AUTH_RECOVERS "$(tr '\n' ' ' < "$OUT_FILE" | head -c 900)"
fi

test_end
