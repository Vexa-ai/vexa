#!/usr/bin/env bash
# Pull latest :dev and restart the lite container. Keeps Postgres state.
# Use reset-lite.sh for a full wipe including DB.
set -euo pipefail

cd /root/vexa
git fetch origin dev
git reset --hard origin/dev

docker pull vexaai/vexa-lite:dev 2>&1 | tail -3
docker stop vexa-lite 2>/dev/null || true
docker rm -f vexa-lite 2>/dev/null || true
# vexa-postgres stays up — keeping state
make lite 2>&1 | tail -10
