#!/usr/bin/env bash
# Fresh reset for a compose deployment — wipes state (containers + volumes),
# brings the stack back up clean. Does NOT reinstall the VM or re-clone vexa.
#
# Runs on the compose VM via vm-run.sh. Assumes /root/vexa exists.
set -euo pipefail

cd /root/vexa

# Pull latest dev branch first so the reset uses current test + deploy scripts.
echo "  [reset-compose] git fetch + reset to origin/dev"
git fetch origin dev 2>&1 | tail -3
git reset --hard origin/dev 2>&1 | tail -2

cd /root/vexa/deploy/compose

# compose needs --env-file; IMAGE_TAG lives in the repo's .env
ENV_FILE="/root/vexa/.env"
[ -f /root/.env ] && ENV_FILE="/root/.env"
echo "  [reset-compose] env file: $ENV_FILE"

echo "  [reset-compose] docker compose down -v"
docker compose --env-file "$ENV_FILE" down --volumes --remove-orphans 2>&1 | tail -5 || true

# Wipe tests3/.state on the VM — otherwise stale api_token etc. from pre-reset
# survive and point at DB rows that no longer exist.
echo "  [reset-compose] wiping tests3/.state (stale creds from prior run)"
rm -rf /root/vexa/tests3/.state 2>/dev/null || true
mkdir -p /root/vexa/tests3/.state

# Purge any stragglers
for c in $(docker ps -a --format '{{.Names}}' | grep -E '^(vexa-|meeting-)' || true); do
    docker rm -f "$c" 2>/dev/null || true
done

echo "  [reset-compose] docker compose up -d --pull always"
docker compose --env-file "$ENV_FILE" up -d --pull always 2>&1 | tail -5

# Wait for core services to become healthy
echo "  [reset-compose] waiting for services..."
for i in $(seq 1 45); do
    if curl -sf http://localhost:8056/ > /dev/null 2>&1; then
        echo "  [reset-compose] gateway up (after ${i}s)"
        break
    fi
    sleep 2
done

# Re-populate tests3/.state/ URLs (gateway_url, admin_url, dashboard_url, deploy_mode).
# We wiped the state dir above; subsequent tests need these to be present.
echo "  [reset-compose] re-running detect to populate URLs"
DEPLOY_MODE=compose bash /root/vexa/tests3/lib/detect.sh 2>&1 | tail -3 || true

# Also wait briefly for the dashboard (not just gateway) — dashboard-auth.sh
# hits the dashboard directly and needs it up at test-start time.
echo "  [reset-compose] waiting for dashboard..."
for i in $(seq 1 30); do
    if curl -sf -o /dev/null http://localhost:3001/ 2>/dev/null; then
        echo "  [reset-compose] dashboard up (after ${i}s)"
        break
    fi
    sleep 2
done
