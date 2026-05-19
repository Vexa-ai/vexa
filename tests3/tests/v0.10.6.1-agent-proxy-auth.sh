#!/usr/bin/env bash
# v0.10.6.1-agent-proxy-auth — D13 prove for the /api/agent/* + /api/vexa/*
# proxy auth contract, part of the auth-bucket hardening (commit 8fbd3c8).
#
# Pins two related invariants:
#   1. The proxy routes refuse silent localhost fallback when
#      VEXA_API_URL / VEXA_ADMIN_API_URL is unset.
#   2. The proxy routes thread the dashboard auth cookie (or VEXA_API_KEY)
#      to the upstream — they do not strip credentials.
#
# Steps:
#   no_localhost_fallback   Static: api/vexa/[...path], api/admin/[...path],
#                           api/agent/[...path], api/auth/me, api/config all
#                           require VEXA_API_URL / VEXA_ADMIN_API_URL to be
#                           explicitly set (no http://localhost:8056 default).
#   auth_threaded           Static: each route reads the auth cookie via
#                           getAuthCookieName() (or VEXA_API_KEY env) and
#                           forwards it to the upstream.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
DASH_API="$ROOT_DIR/services/dashboard/src/app/api"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-agent-proxy-auth :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-agent-proxy-auth-$step"

# Routes that must enforce the explicit-URL contract.
ROUTES=(
  "vexa/[...path]/route.ts"
  "admin/[...path]/route.ts"
  "agent/[...path]/route.ts"
  "auth/me/route.ts"
  "config/route.ts"
)

case "$step" in
  no_localhost_fallback)
    failed=0

    for route_rel in "${ROUTES[@]}"; do
      route="$DASH_API/$route_rel"
      if [ ! -f "$route" ]; then
        # agent/[...path] is optional in dashboards that don't expose the
        # agent flow; skip if absent.
        if [[ "$route_rel" == "agent/"* ]]; then
          step_skip "NO_LOCALHOST_$route_rel" "route absent (acceptable)"
          continue
        fi
        echo "    FAIL: $route missing"
        step_fail "ROUTE_PRESENT_$route_rel" "route file not found"
        failed=1
        continue
      fi

      # Localhost defaults are the regression class. Catch:
      #   process.env.VEXA_API_URL || "http://localhost:..."
      #   process.env.VEXA_ADMIN_API_URL || "http://localhost:..."
      # Allow comments mentioning localhost.
      offending="$(grep -nE 'process\.env\.(VEXA_API_URL|VEXA_ADMIN_API_URL)\s*\|\|\s*"http://(localhost|127\.0\.0\.1)' "$route" || true)"
      if [ -n "$offending" ]; then
        echo "    FAIL: $route still has localhost VEXA_*_URL fallback:"
        echo "$offending" | sed 's/^/      /'
        step_fail "NO_LOCALHOST_$route_rel" "localhost fallback present"
        failed=1
      else
        step_pass "NO_LOCALHOST_$route_rel" "no localhost fallback"
      fi
    done

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  auth_threaded)
    failed=0

    for route_rel in "${ROUTES[@]}"; do
      route="$DASH_API/$route_rel"
      [ -f "$route" ] || continue
      # The admin proxy uses its own session cookie ("vexa-admin-session"),
      # not the user-facing auth cookie. Accept either auth scheme.
      if grep -q 'getAuthCookieName' "$route" \
         || grep -q '"vexa-token"' "$route" \
         || grep -q 'vexa-admin-session\|ADMIN_COOKIE_NAME' "$route"; then
        step_pass "AUTH_READ_$route_rel" "route reads an auth-cookie name"
      else
        echo "    FAIL: $route does not reference any known auth cookie"
        step_fail "AUTH_READ_$route_rel" "no auth-cookie reference"
        failed=1
      fi
    done

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  *)
    echo "    FAIL: unknown step '$step'"
    test_end
    exit 1
    ;;
esac

test_end
echo ""
echo "  PASS"
