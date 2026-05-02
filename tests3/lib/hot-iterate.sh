#!/usr/bin/env bash
# Hot-iterate dev loop — fast feedback for code changes during develop stage.
#
# Use this when you've made a small code change and want to validate it on
# compose-mode in <5 min instead of triggering a full release-deploy +
# release-validate matrix (~30 min).
#
# What it does:
#   1. Builds ONLY the specified service image (not all 8+)
#   2. Pushes :dev tag to DockerHub
#   3. SSH to compose VM, docker compose pull + force-recreate that service
#   4. Run scope-filtered tests on compose only (skips reset, skips lite/helm)
#
# When to STOP iterating and switch to authoritative release-deploy +
# release-validate matrix:
#   - compose-mode tests passed
#   - You're about to enter `human` stage
#   - Authoritative validation MUST be fresh + cross-mode (lite + compose + helm).
#
# Usage:
#   bash tests3/lib/hot-iterate.sh <service> [<scope-yaml>]
#
# Examples:
#   bash tests3/lib/hot-iterate.sh vexa-bot
#   bash tests3/lib/hot-iterate.sh dashboard tests3/releases/<id>/scope.yaml
#   bash tests3/lib/hot-iterate.sh meeting-api
#
# Service names match docker-compose.yml service names AND the image
# repository name pattern `vexaai/<service>`.

set -euo pipefail

SERVICE="${1:-}"
SCOPE="${2:-}"

if [[ -z "$SERVICE" ]]; then
    echo "usage: bash tests3/lib/hot-iterate.sh <service> [<scope-yaml>]" >&2
    echo "  services: vexa-bot dashboard meeting-api runtime-api admin-api api-gateway mcp tts-service vexa-lite" >&2
    exit 2
fi

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
COMPOSE_VM_IP=$(cat "$ROOT_DIR/tests3/.state-compose/vm_ip" 2>/dev/null || echo "")
if [[ -z "$COMPOSE_VM_IP" ]]; then
    echo "FAIL: tests3/.state-compose/vm_ip not found — provision compose VM first" >&2
    exit 2
fi

# Map service name → Dockerfile path + build context. Most services live under
# services/<name>/ with their own Dockerfile; bot has a non-standard layout.
case "$SERVICE" in
    vexa-bot)
        DOCKERFILE="$ROOT_DIR/services/vexa-bot/Dockerfile"
        CONTEXT="$ROOT_DIR/services/vexa-bot"
        IMAGE="vexaai/vexa-bot"
        COMPOSE_SVC="bot"  # docker-compose service name
        ;;
    dashboard|meeting-api|runtime-api|admin-api|api-gateway|mcp|tts-service)
        DOCKERFILE="$ROOT_DIR/services/$SERVICE/Dockerfile"
        CONTEXT="$ROOT_DIR"  # repo root for shared libs/
        IMAGE="vexaai/$SERVICE"
        COMPOSE_SVC="$SERVICE"
        ;;
    vexa-lite)
        DOCKERFILE="$ROOT_DIR/deploy/lite/Dockerfile.lite"
        CONTEXT="$ROOT_DIR"
        IMAGE="vexaai/vexa-lite"
        COMPOSE_SVC=""  # lite isn't part of compose stack; this branch just rebuilds + pushes
        ;;
    *)
        echo "FAIL: unknown service '$SERVICE'" >&2
        exit 2
        ;;
esac

echo "=== HOT-ITERATE: $SERVICE → compose VM at $COMPOSE_VM_IP ==="
echo ""

# ─── 1. Build only this service's image ───────────────────────
TAG="hot-$(date +%H%M%S)"
echo "[1/4] docker build $IMAGE:$TAG"
docker build --platform linux/amd64 \
    -t "$IMAGE:$TAG" \
    -t "$IMAGE:dev" \
    -f "$DOCKERFILE" "$CONTEXT" 2>&1 | tail -3

# ─── 2. Push :dev tag ───────────────────────────────────────────
echo ""
echo "[2/4] docker push $IMAGE:dev"
docker push "$IMAGE:dev" 2>&1 | tail -3

# ─── 3. Recreate just this service on compose VM ────────────────
if [[ -n "$COMPOSE_SVC" ]]; then
    echo ""
    echo "[3/4] ssh $COMPOSE_VM_IP — pull + recreate $COMPOSE_SVC"
    ssh -o StrictHostKeyChecking=no -o BatchMode=yes "root@$COMPOSE_VM_IP" \
        "cd /root/vexa/deploy/compose && \
         docker compose --env-file /root/vexa/.env pull $COMPOSE_SVC 2>&1 | tail -3 && \
         docker compose --env-file /root/vexa/.env up -d --force-recreate --no-deps $COMPOSE_SVC 2>&1 | tail -3"
else
    echo ""
    echo "[3/4] (skipped — $SERVICE not part of compose stack)"
fi

# ─── 4. Scope-filtered tests on compose only ───────────────────
if [[ -n "$SCOPE" && -f "$SCOPE" ]]; then
    echo ""
    echo "[4/4] scope-filtered tests on compose VM (SCOPE=$SCOPE)"
    make -C "$ROOT_DIR/tests3" vm-validate-scope-compose \
        STATE="$ROOT_DIR/tests3/.state-compose" \
        SCOPE="$SCOPE"
else
    echo ""
    echo "[4/4] (skipped — no SCOPE provided; pass scope.yaml as 2nd arg to validate)"
fi

echo ""
echo "=== HOT-ITERATE done — verified $SERVICE on compose ==="
echo ""
echo "When iteration converges (compose tests pass), switch to authoritative"
echo "validation across all 3 modes:"
echo "  make release-deploy SCOPE=<scope>      # rebuilds + pushes + redeploys lite + compose + helm"
echo "  make release-validate SCOPE=<scope>    # full fresh matrix → human/triage"
