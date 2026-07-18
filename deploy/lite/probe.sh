#!/usr/bin/env bash
# =============================================================================
# deploy/lite/probe.sh — `make probe SURFACE=lite` (the most divergent path).
#
# The Lite surface's wrapper around the shared full-journey probe
# (scripts/probe/journey.sh): mints a scoped API key through the admin-api
# INSIDE the vexa-lite container (its port moved between images — autodetect,
# like the release smoke does), then drives the journey through the published
# gateway front door. Lite runs the REAL bot as an in-container process, so the
# mode is `real`: a dead synthetic meeting → a truthful named terminal.
#
# Overrides: GATEWAY_URL · VEXA_API_KEY · ADMIN_TOKEN · APP_CONTAINER · PROBE_* (see journey.sh)
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

APP="${APP_CONTAINER:-vexa-lite}"
GW_PORT="${HOST_GATEWAY_PORT:-8056}"
GATEWAY_URL="${GATEWAY_URL:-http://localhost:$GW_PORT}"
ADMIN_TOKEN="${ADMIN_TOKEN:-changeme}"

X() { docker exec "$APP" "$@"; }

if [ -z "${VEXA_API_KEY:-}" ]; then
  # admin-api port moved between images (8057 → 8001) — autodetect, don't assume.
  ADMIN_PORT=""
  for p in 8001 8057; do
    code="$(X curl -s -m 3 -o /dev/null -w '%{http_code}' "http://localhost:$p/admin/users" \
            -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null || true)"
    case "$code" in 2*|4*) ADMIN_PORT="$p"; break;; esac
  done
  [ -n "$ADMIN_PORT" ] || { echo "probe: admin-api not reachable on 8001/8057 inside $APP" >&2; exit 1; }
  ADMIN="http://localhost:$ADMIN_PORT"
  uid="$(X curl -s "$ADMIN/admin/users/email/probe@vexa.ai" -H "X-Admin-API-Key: $ADMIN_TOKEN" \
         | sed -n 's/.*"id":\([0-9]*\).*/\1/p')"
  if [ -z "$uid" ]; then
    uid="$(X curl -s -X POST "$ADMIN/admin/users" -H "X-Admin-API-Key: $ADMIN_TOKEN" \
           -H 'Content-Type: application/json' \
           -d '{"email":"probe@vexa.ai","name":"Probe","max_concurrent_bots":3}' \
           | sed -n 's/.*"id":\([0-9]*\).*/\1/p')"
  fi
  [ -n "$uid" ] || { echo "probe: could not create/find the probe user" >&2; exit 1; }
  VEXA_API_KEY="$(X curl -s -X POST "$ADMIN/admin/users/$uid/tokens?scopes=bot,tx" \
                  -H "X-Admin-API-Key: $ADMIN_TOKEN" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')"
  [ -n "$VEXA_API_KEY" ] || { echo "probe: could not mint a bot,tx token" >&2; exit 1; }
fi

# The one-shot all-component sweep: the single container's supervised services + every
# spawned bot workload's log — the whole Lite failure set in one read.
PROBE_SWEEP_CMD="
echo '--- $APP (tail 60) ---'; docker logs --tail 60 '$APP' 2>&1;
echo '--- bot workload logs (/tmp/vexa-workloads) ---';
docker exec '$APP' bash -c 'for f in /tmp/vexa-workloads/*.log; do
  [ -e \"\$f\" ] || { echo \"(no workload logs)\"; break; };
  echo \"--- \$f (tail 25) ---\"; tail -25 \"\$f\"; done' 2>&1"

PROBE_MODE="${PROBE_MODE:-real}"
export GATEWAY_URL VEXA_API_KEY PROBE_MODE PROBE_SWEEP_CMD
exec "$ROOT/scripts/probe/journey.sh"
