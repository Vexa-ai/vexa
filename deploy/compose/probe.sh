#!/usr/bin/env bash
# =============================================================================
# deploy/compose/probe.sh — `make probe SURFACE=compose` (the fast default).
#
# The compose surface's wrapper around the shared full-journey probe
# (scripts/probe/journey.sh): resolves the gateway front door + admin-api from
# .env, mints a scoped API key (bin/provision-token — idempotent), picks the
# mode from the stack's BROWSER_IMAGE (mock-bot → the deterministic green
# journey; anything else → the truthful dead-URL journey), and wires the
# all-component `docker compose logs` sweep.
#
# Overrides: GATEWAY_URL · VEXA_API_KEY · ADMIN_TOKEN · PROBE_MODE · PROBE_* (see journey.sh)
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$HERE"

envv() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//' || true; }

GW_PORT="$(envv API_GATEWAY_HOST_PORT)"; GW_PORT="${GW_PORT:-18056}"
ADMIN_PORT="$(envv ADMIN_API_PORT)";     ADMIN_PORT="${ADMIN_PORT:-18057}"
PROJECT="${COMPOSE_PROJECT:-$(envv COMPOSE_PROJECT_NAME)}"; PROJECT="${PROJECT:-vexa-v012}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:$GW_PORT}"

if [ -z "${VEXA_API_KEY:-}" ]; then
  ADMIN_TOKEN="${ADMIN_TOKEN:-$(envv ADMIN_TOKEN)}"
  : "${ADMIN_TOKEN:?no VEXA_API_KEY given and no ADMIN_TOKEN in env/.env to mint one with}"
  VEXA_API_KEY="$(ADMIN_TOKEN="$ADMIN_TOKEN" ADMIN_API_URL="http://127.0.0.1:$ADMIN_PORT" \
    EMAIL=probe@vexa.ai SCOPES=bot,tx ./bin/provision-token)"
fi

if [ -z "${PROBE_MODE:-}" ]; then
  BROWSER_IMAGE="${BROWSER_IMAGE:-$(envv BROWSER_IMAGE)}"
  case "$BROWSER_IMAGE" in *mock*) PROBE_MODE=mock;; *) PROBE_MODE=real;; esac
fi

# The one-shot all-component sweep: every service's tail together, plus any bot containers
# the runtime spawned (vexa-mtg-…) — the parallel failure inventory, gathered ONCE.
PROBE_SWEEP_CMD="
docker compose -p '$PROJECT' -f '$HERE/docker-compose.yml' logs --tail=40 2>&1;
echo '--- spawned bot containers (vexa-mtg-*) ---';
docker ps -a --filter name=vexa-mtg --format '{{.Names}}  {{.Status}}  {{.Image}}' | head -10;
for c in \$(docker ps -aq --filter name=vexa-mtg | head -3); do
  echo \"--- \$c (tail 30) ---\"; docker logs --tail 30 \"\$c\" 2>&1;
done"

export GATEWAY_URL VEXA_API_KEY PROBE_MODE PROBE_SWEEP_CMD
exec "$ROOT/scripts/probe/journey.sh"
