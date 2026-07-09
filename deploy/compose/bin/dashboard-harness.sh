#!/usr/bin/env bash
# dashboard-harness.sh — the dashboard FULL-SURFACE proof (send-bot + live WS transcript), end to end.
#
# Brings the control-plane stack up with the MOCK bot (deterministic transcript, no live meeting/STT),
# mints a scoped token, brings the dashboard up self-host-authed with it, then runs dashboard_surface.py
# (config · send-bot via the dashboard proxy · live WS transcript). Isolated compose project; tears down.
#
#   make -C deploy/compose dashboard-harness        # (or) ./bin/dashboard-harness.sh
#
# Needs: mock-bot:dev + vexa-dashboard:dev images built; docker; uv/python3. GREEN-OR-SKIP on no-docker.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/.."
export PATH="$HOME/.local/bin:$PATH"

if ! docker info >/dev/null 2>&1; then echo "  ↳ dashboard-harness — docker not available → skip"; exit 0; fi

PROJ="${COMPOSE_PROJECT:-vexa-dash}"
ADMIN="${ADMIN_TOKEN:-gate-admin-token}"
export IMAGE_TAG=dev COMPOSE_PROJECT_NAME="$PROJ" ADMIN_TOKEN="$ADMIN" \
       INTERNAL_API_SECRET="${INTERNAL_API_SECRET:-gate-internal-secret}" MINIO_BUCKET=vexa \
       BROWSER_IMAGE="${BROWSER_IMAGE:-mock-bot:dev}" DOCKER_GID="${DOCKER_GID:-0}" \
       MINIO_HOST_PORT="${MINIO_HOST_PORT:-19000}"
DC=(docker compose -p "$PROJ" -f docker-compose.yml -f docker-compose.dashboard.yml)

cleanup() {
  "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  names=$(docker ps -aq --filter "name=vexa-mtg-" 2>/dev/null || true); [ -n "$names" ] && docker rm -f $names >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[harness] control-plane up (BROWSER_IMAGE=$BROWSER_IMAGE) …"
"${DC[@]}" up -d gateway meeting-api runtime admin-api postgres redis minio minio-init >/dev/null
until curl -sf localhost:18080/health >/dev/null 2>&1 && curl -sf localhost:18056/health >/dev/null 2>&1; do sleep 2; done
echo "[harness] stack healthy"

echo "[harness] mint a scoped token …"
U=$(curl -s -XPOST localhost:18057/admin/users -H "X-Admin-API-Key: $ADMIN" -H "Content-Type: application/json" \
      -d "{\"email\":\"dash-$RANDOM@vexa.ai\",\"name\":\"dash\",\"max_concurrent_bots\":5}" \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
TOK=$(curl -s -XPOST "localhost:18057/admin/users/$U/tokens?scopes=bot,tx" -H "X-Admin-API-Key: $ADMIN" \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
echo "[harness] token minted (user $U)"

echo "[harness] dashboard up (self-host auth) …"
DASHBOARD_API_KEY="$TOK" "${DC[@]}" up -d dashboard >/dev/null
until curl -sf localhost:18030/api/config >/dev/null 2>&1; do sleep 2; done
echo "[harness] dashboard healthy → http://localhost:18030"

echo "[harness] running the full-surface proof …"
set +e
DASHBOARD_URL=http://127.0.0.1:18030 uv run --quiet python tests/dashboard_surface.py
RC=$?
set -e
echo "[harness] verdict: $([ $RC -eq 0 ] && echo PASS || echo FAIL)"
exit $RC
