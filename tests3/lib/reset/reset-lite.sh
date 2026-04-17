#!/usr/bin/env bash
# Fresh reset for a lite deployment — stops and removes the vexa-lite + vexa-postgres
# containers, drops the PG data volume, and re-runs `make lite` for a clean start.
#
# Runs on the lite VM via vm-run.sh. Assumes /root/vexa exists.
set -euo pipefail

cd /root/vexa

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
