#!/usr/bin/env bash
# dashboard-browser-auth — real browser auth probe for /meetings.

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
    *) DASHBOARD_URL="http://localhost:3001" ;;
  esac
fi

test_begin "dashboard-browser-auth"

if ! node -e "require('playwright')" >/dev/null 2>&1; then
  step_skip DASHBOARD_BROWSER_MEETINGS_AUTH_OK "playwright unavailable in this env (e.g. headless VM) — browser auth probe skipped; covered locally"
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

OUT_FILE="$(mktemp -t dashboard-browser-auth-XXXXXX.log)"
trap 'rm -f "$OUT_FILE"' EXIT

if DASHBOARD_URL="$DASHBOARD_URL" node "$SCRIPT_DIR/dashboard-browser-auth.mjs" >"$OUT_FILE" 2>&1; then
  step_pass DASHBOARD_BROWSER_MEETINGS_AUTH_OK "$(tr '\n' ' ' < "$OUT_FILE" | head -c 300)"
else
  step_fail DASHBOARD_BROWSER_MEETINGS_AUTH_OK "$(tr '\n' ' ' < "$OUT_FILE" | head -c 700)"
fi

test_end
