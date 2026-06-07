#!/usr/bin/env bash
# dashboard-cookie-isolation — cross-deployment localhost browser auth probe.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

DASHBOARD_URLS="${DASHBOARD_URLS:-http://localhost:3100,http://localhost:3001}"

test_begin "dashboard-cookie-isolation"

if ! node -e "require('playwright')" >/dev/null 2>&1; then
  step_skip DASHBOARD_AUTH_COOKIES_ISOLATED "playwright unavailable in this env (e.g. headless VM) — cookie isolation probe skipped; covered locally"
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

OUT_FILE="$(mktemp -t dashboard-cookie-isolation-XXXXXX.log)"
trap 'rm -f "$OUT_FILE"; _flush_test_report' EXIT INT TERM

if DASHBOARD_URLS="$DASHBOARD_URLS" node "$SCRIPT_DIR/dashboard-cookie-isolation.mjs" >"$OUT_FILE" 2>&1; then
  step_pass DASHBOARD_AUTH_COOKIES_ISOLATED "$(tr '\n' ' ' < "$OUT_FILE" | head -c 400)"
else
  step_fail DASHBOARD_AUTH_COOKIES_ISOLATED "$(tr '\n' ' ' < "$OUT_FILE" | head -c 900)"
fi

test_end
