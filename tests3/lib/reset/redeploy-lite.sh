#!/usr/bin/env bash
# Pull latest :dev and restart the lite container. Keeps Postgres state.
# Use reset-lite.sh for a full wipe including DB.
set -euo pipefail

cd /root/vexa

# Pull the branch this VM was provisioned with (VM_BRANCH from vm-reset.sh,
# sourced from tests3/.state-<mode>/vm_branch). Cycles run on release/<id>;
# hardcoding `dev` doesn't generalise — `dev` may not exist on the remote.
: "${VM_BRANCH:?VM_BRANCH must be set (sourced from tests3/.state-<mode>/vm_branch by vm-reset.sh)}"
git fetch origin "${VM_BRANCH}"
git reset --hard "origin/${VM_BRANCH}"

docker pull vexaai/vexa-lite:dev 2>&1 | tail -3
docker stop vexa-lite 2>/dev/null || true
docker rm -f vexa-lite 2>/dev/null || true
# vexa-postgres stays up — keeping state
make lite 2>&1 | tail -10
