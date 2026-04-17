#!/usr/bin/env bash
# Fresh reset for a compose deployment — wipes state (containers + volumes),
# brings the stack back up clean. Does NOT reinstall the VM or re-clone vexa.
#
# Runs on the compose VM via vm-run.sh. Assumes /root/vexa exists.
set -euo pipefail

cd /root/vexa/deploy/compose

echo "  [reset-compose] docker compose down -v"
docker compose down --volumes --remove-orphans 2>&1 | tail -5 || true

# Purge any stragglers
for c in $(docker ps -a --format '{{.Names}}' | grep -E '^(vexa-|meeting-)' || true); do
    docker rm -f "$c" 2>/dev/null || true
done

echo "  [reset-compose] docker compose up -d --pull always"
docker compose up -d --pull always 2>&1 | tail -5

# Wait for core services to become healthy
echo "  [reset-compose] waiting for services..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8056/ > /dev/null 2>&1; then
        echo "  [reset-compose] gateway up (after ${i}s)"
        break
    fi
    sleep 2
done
