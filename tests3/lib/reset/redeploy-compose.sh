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

# Some deployments store IMAGE_TAG in /root/.env, others in /root/vexa/.env
ENV_FILE="/root/vexa/.env"
[ -f /root/.env ] && ENV_FILE="/root/.env"

echo "  [redeploy-compose] using env: $ENV_FILE"
docker compose --env-file "$ENV_FILE" pull 2>&1 | tail -5
docker compose --env-file "$ENV_FILE" up -d --force-recreate 2>&1 | tail -5
