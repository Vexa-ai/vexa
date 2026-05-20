#!/usr/bin/env bash
# Pull latest :dev and restart the compose stack on a compose VM.
# Keeps state (volumes) — use reset-compose.sh for a clean wipe.
set -euo pipefail

cd /root/vexa

# Pull the branch this VM was provisioned with (passed via VM_BRANCH from
# vm-reset.sh, sourced from tests3/.state-<mode>/vm_branch). Hardcoding
# `dev` is wrong — the remote may not have a `dev` branch at all (many
# release flows run release/<id> branches that merge directly to main).
: "${VM_BRANCH:?VM_BRANCH must be set (sourced from tests3/.state-<mode>/vm_branch by vm-reset.sh)}"
git fetch origin "${VM_BRANCH}"
git reset --hard "origin/${VM_BRANCH}"
cd deploy/compose

ENV_FILE="/root/vexa/.env"

echo "  [redeploy-compose] using env: $ENV_FILE"
if [ -n "${VM_IMAGE_TAG:-}" ]; then
    for f in "$ENV_FILE" /root/.env; do
        [ -f "$f" ] || continue
        sed -i "s|^#*IMAGE_TAG=.*|IMAGE_TAG=${VM_IMAGE_TAG}|" "$f"
        sed -i "s|^#*BROWSER_IMAGE=.*|BROWSER_IMAGE=vexaai/vexa-bot:${VM_IMAGE_TAG}|" "$f"
    done
    echo "  [redeploy-compose] pinned IMAGE_TAG=${VM_IMAGE_TAG}"
fi
docker compose --env-file "$ENV_FILE" pull 2>&1 | tail -5
BOT_IMAGE="$(grep -E '^BROWSER_IMAGE=' "$ENV_FILE" 2>/dev/null | cut -d= -f2-)"
if [ -n "$BOT_IMAGE" ]; then
    echo "  [redeploy-compose] pulling runtime bot image: $BOT_IMAGE"
    docker pull "$BOT_IMAGE" 2>&1 | tail -3
fi
docker compose --env-file "$ENV_FILE" up -d --force-recreate 2>&1 | tail -5

# v0.10.5 R3 follow-up (#272 iter-5): re-validate dashboard's VEXA_API_KEY
# against the gateway and regenerate it if stale. The Makefile target was
# updated in iter-2 to probe-and-regenerate; this redeploy script needs
# to actually invoke it after `up`. Without this, redeploys leave a stale
# token in .env from a previous admin-api state, causing
# DASHBOARD_API_KEY_VALID to fail on every compose validate even though
# the underlying logic is correct.
echo "  [redeploy-compose] reseating dashboard VEXA_API_KEY (probe-and-regenerate)..."
# Wait briefly for admin-api to be reachable post-up
for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf "http://localhost:8056/admin/users?limit=1" \
        -H "X-Admin-API-Key: $(grep -E '^ADMIN_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo changeme)" \
        >/dev/null 2>&1; then
        break
    fi
    sleep 2
done
make setup-api-key 2>&1 | tail -3 || echo "  [redeploy-compose] setup-api-key reported non-zero; continuing"
docker compose --env-file "$ENV_FILE" up -d --force-recreate dashboard 2>&1 | tail -5 || true
