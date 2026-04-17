#!/usr/bin/env bash
# Fresh reset for a lite deployment — stops and removes the vexa-lite + vexa-postgres
# containers, drops the PG data volume, and re-runs `make lite` for a clean start.
#
# Runs on the lite VM via vm-run.sh. Assumes /root/vexa exists.
set -euo pipefail

cd /root/vexa

# Pull latest dev branch first so the reset uses current test + deploy scripts.
echo "  [reset-lite] git fetch + reset to origin/dev"
git fetch origin dev 2>&1 | tail -3
git reset --hard origin/dev 2>&1 | tail -2

# Wipe tests3/.state on the VM — otherwise stale api_token etc. from pre-reset
# survive and point at DB rows that no longer exist.
echo "  [reset-lite] wiping tests3/.state (stale creds from prior run)"
rm -rf /root/vexa/tests3/.state 2>/dev/null || true
mkdir -p /root/vexa/tests3/.state

echo "  [reset-lite] stopping containers"
docker stop vexa-lite 2>/dev/null || true
docker rm -f vexa-lite 2>/dev/null || true
docker stop vexa-postgres 2>/dev/null || true
docker rm -f vexa-postgres 2>/dev/null || true

# Drop postgres data so migrations start fresh. Lite's PG uses default volume.
docker volume ls -q | grep -E '^vexa-' | xargs -r docker volume rm -f 2>/dev/null || true

echo "  [reset-lite] make lite"
make lite 2>&1 | tail -10

# Wait for gateway to respond
echo "  [reset-lite] waiting for services..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:8056/ > /dev/null 2>&1; then
        echo "  [reset-lite] gateway up (after ${i}s)"
        break
    fi
    sleep 2
done

# Re-populate tests3/.state/ URLs. We wiped the state dir above.
echo "  [reset-lite] re-running detect to populate URLs"
DEPLOY_MODE=lite bash /root/vexa/tests3/lib/detect.sh 2>&1 | tail -3 || true

# Wait for dashboard
echo "  [reset-lite] waiting for dashboard..."
for i in $(seq 1 30); do
    if curl -sf -o /dev/null http://localhost:3000/ 2>/dev/null; then
        echo "  [reset-lite] dashboard up (after ${i}s)"
        break
    fi
    sleep 2
done
