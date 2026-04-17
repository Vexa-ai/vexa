#!/usr/bin/env bash
# Pull latest :dev and restart the compose stack on a compose VM.
# Keeps state (volumes) — use reset-compose.sh for a clean wipe.
set -euo pipefail

cd /root/vexa
git fetch origin dev
git reset --hard origin/dev
cd deploy/compose

# Some deployments store IMAGE_TAG in /root/.env, others in /root/vexa/.env
ENV_FILE="/root/vexa/.env"
[ -f /root/.env ] && ENV_FILE="/root/.env"

echo "  [redeploy-compose] using env: $ENV_FILE"
docker compose --env-file "$ENV_FILE" pull 2>&1 | tail -5
docker compose --env-file "$ENV_FILE" up -d --force-recreate 2>&1 | tail -5
