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

ENV_FILE="/root/vexa/.env"
if [ -n "${VM_IMAGE_TAG:-}" ]; then
    for f in "$ENV_FILE" /root/.env; do
        [ -f "$f" ] || continue
        sed -i "s|^#*IMAGE_TAG=.*|IMAGE_TAG=${VM_IMAGE_TAG}|" "$f"
        sed -i "s|^#*BROWSER_IMAGE=.*|BROWSER_IMAGE=vexaai/vexa-bot:${VM_IMAGE_TAG}|" "$f"
    done
    echo "  [redeploy-lite] pinned IMAGE_TAG=${VM_IMAGE_TAG}"
fi

IMAGE_TAG="$(grep -E '^IMAGE_TAG=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)"
IMAGE_TAG="${IMAGE_TAG:-latest}"
docker pull "vexaai/vexa-lite:${IMAGE_TAG}" 2>&1 | tail -3
docker stop vexa-lite 2>/dev/null || true
docker rm -f vexa-lite 2>/dev/null || true

# v0.10.6 Pack U.7 follow-up — clean squatter containers from any stray
# `docker compose` run on the same VM. Lite's canonical entry uses
# `vexa-postgres` / `vexa-redis` (no suffix); compose's docker-compose
# pattern creates `vexa-postgres-1` / `vexa-redis-1` squatting the same
# host ports. Without this cleanup, `make lite` later prints
# "ERROR: PostgreSQL server at localhost:5432 is not reachable" and
# vexa-lite stays in CrashLoop. Caught 2026-05-03 release-validate:
# 14/15 DoD failures cascaded from a port-conflict at 5432.
for squatter in vexa-postgres-1 vexa-redis-1; do
    if docker ps -aq -f "name=^${squatter}$" 2>/dev/null | grep -q .; then
        echo "  [redeploy-lite] removing squatter container: $squatter"
        docker stop "$squatter" 2>/dev/null || true
        docker rm -f "$squatter" 2>/dev/null || true
    fi
done

# vexa-postgres stays up — keeping state
set -o pipefail
make -C deploy/lite preflight up init-db test 2>&1 | tail -10

# v0.10.6 Pack U.7 follow-up — verify the gateway actually came up.
# `make lite` prints success markers but a port-conflict or DB-init failure
# can leave the lite container alive with services down. Fail loud here so
# release-deploy doesn't proceed past a half-up lite VM (caught 2026-05-03 —
# release-validate showed 14 lite-down DoDs as gate-fail despite redeploy
# reporting "running").
echo "  [redeploy-lite] verifying gateway responds..."
for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf -m 3 http://localhost:8056/openapi.json -o /dev/null; then
        echo "  [redeploy-lite] gateway up after ${i} attempt(s)"
        break
    fi
    if [ "$i" = "10" ]; then
        echo "  [redeploy-lite] FAIL: gateway at localhost:8056 not responding after 10×3s" >&2
        exit 1
    fi
    sleep 3
done
