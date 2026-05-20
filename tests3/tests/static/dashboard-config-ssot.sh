#!/usr/bin/env bash
# dashboard-config-ssot — dashboard deployment URLs must come from explicit
# release/deploy configuration, not baked localhost defaults or runtime rewrites.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin "dashboard-config-ssot"

SEARCH_PATHS=(
  "$ROOT_DIR/services/dashboard/.env.example"
  "$ROOT_DIR/services/dashboard/Dockerfile"
  "$ROOT_DIR/services/dashboard/docker-entrypoint.sh"
  "$ROOT_DIR/services/dashboard/next.config.ts"
  "$ROOT_DIR/services/dashboard/validate.sh"
  "$ROOT_DIR/services/dashboard/src"
)

bad_urls="$(rg -n 'localhost:(8066|18056)|ws://localhost:3001|VEXA_API_URL=http://localhost:8066' "${SEARCH_PATHS[@]}" 2>/dev/null || true)"
if [ -z "$bad_urls" ]; then
  step_pass DASHBOARD_CONFIG_NO_STALE_LOCALHOST_DEFAULTS "dashboard config has no stale baked localhost gateway/websocket defaults"
else
  step_fail DASHBOARD_CONFIG_NO_STALE_LOCALHOST_DEFAULTS "$(printf '%s' "$bad_urls" | head -10 | tr '\n' ' ')"
fi

config_route="$ROOT_DIR/services/dashboard/src/app/api/config/route.ts"
runtime_public_api="$(rg -n 'process\.env\.VEXA_PUBLIC_API_URL|process\.env\.NEXT_PUBLIC_VEXA_API_URL|process\.env\.NEXT_PUBLIC_API_URL' "$config_route" 2>/dev/null || true)"
runtime_ws_derivation="$(rg -n 'wsUrlFromHttpBase\(publicApiUrl\)|if \(publicApiUrl\)' "$config_route" 2>/dev/null || true)"
loopback_normalization="$(rg -n 'isLoopbackHost|new URL\(configuredPublicApiUrl\)|configured\.hostname = requestHostname' "$config_route" 2>/dev/null || true)"
entrypoint_patch="$(rg -n 'sed -i|localhost:8066|Patched rewrites' "$ROOT_DIR/services/dashboard/docker-entrypoint.sh" 2>/dev/null || true)"
if [ -n "$runtime_public_api" ] && [ -n "$runtime_ws_derivation" ] && [ -n "$loopback_normalization" ] && [ -z "$entrypoint_patch" ]; then
  step_pass DASHBOARD_REWRITES_REQUIRE_BUILD_SSOT "dashboard /api/config derives browser WS from runtime public API config, normalizes loopback URLs to the request host for remote self-hosted browsers, and keeps build-time rewrites as fallback only"
else
  step_fail DASHBOARD_REWRITES_REQUIRE_BUILD_SSOT "$(printf '%s\n%s\n%s\n%s' "$runtime_public_api" "$runtime_ws_derivation" "$loopback_normalization" "$entrypoint_patch" | head -10 | tr '\n' ' ')"
fi

admin_fan_in="$(rg -n 'VEXA_ADMIN_API_URL[^;\n]*\|\|[^;\n]*VEXA_API_URL|process\.env\.VEXA_ADMIN_API_URL\s*\|\|\s*process\.env\.VEXA_API_URL' "$ROOT_DIR/services/dashboard/src" 2>/dev/null || true)"
if [ -z "$admin_fan_in" ]; then
  step_pass DASHBOARD_ADMIN_URL_EXPLICIT_SSOT "admin routes require VEXA_ADMIN_API_URL explicitly instead of borrowing VEXA_API_URL"
else
  step_fail DASHBOARD_ADMIN_URL_EXPLICIT_SSOT "$(printf '%s' "$admin_fan_in" | head -10 | tr '\n' ' ')"
fi

client_localhost="$(rg -n 'localhost:3001|localhost:8056|localhost:8066|localhost:8765' \
  "$ROOT_DIR/services/dashboard/src/hooks" \
  "$ROOT_DIR/services/dashboard/src/components/meetings/browser-session-view.tsx" 2>/dev/null || true)"
if [ -z "$client_localhost" ]; then
  step_pass DASHBOARD_CLIENT_URLS_FROM_RUNTIME_CONFIG "browser websocket/session helpers derive URLs from /api/config or request origin"
else
  step_fail DASHBOARD_CLIENT_URLS_FROM_RUNTIME_CONFIG "$(printf '%s' "$client_localhost" | head -10 | tr '\n' ' ')"
fi

test_end
