#!/usr/bin/env bash
# =============================================================================
# build-set.sh — build + push the ENTIRE release image set at one tag.
#
# The "release set" is every image a deployment path consumes:
#   - vexaai/vexa-lite        (single-container path; linux/amd64 + arm64)
#   - vexaai/v012-<service>   (compose/k8s path, 8 services; linux/amd64)
#   - vexaai/vexa-bot         (worker the runtime spawns in compose/k8s; linux/amd64)
#
# Every image is stamped with OCI labels so "what commit is this?" is always
# answerable:  org.opencontainers.image.{revision,version,created,source}.
#
# Usage:  deploy/release/build-set.sh vX.Y.Z
# Requires: docker login (push perms on vexaai/*), buildx builder (created if absent).
# The git tree must be clean and on the commit being released.
# =============================================================================
set -euo pipefail

TAG="${1:?usage: build-set.sh vX.Y.Z}"
case "$TAG" in v*.*.*) ;; *) echo "TAG must look like vX.Y.Z (got: $TAG)"; exit 1;; esac

ROOT=$(git rev-parse --show-toplevel); cd "$ROOT"
[ -z "$(git status --porcelain)" ] || { echo "refusing: dirty working tree"; exit 1; }
SHA=$(git rev-parse HEAD)
CREATED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LABELS=(--label "org.opencontainers.image.revision=$SHA"
        --label "org.opencontainers.image.version=$TAG"
        --label "org.opencontainers.image.created=$CREATED"
        --label "org.opencontainers.image.source=https://github.com/Vexa-ai/vexa")
BUILDER=vexa-multiarch
docker buildx inspect $BUILDER >/dev/null 2>&1 || docker buildx create --name $BUILDER --driver docker-container --bootstrap

# name  context  dockerfile — MUST mirror deploy/compose/docker-compose.yml build blocks.
SERVICES=(
  "v012-admin-api    core/identity/services/admin-api  core/identity/services/admin-api/Dockerfile"
  "v012-runtime      core/runtime                      core/runtime/Dockerfile"
  "v012-agent-worker .                                 core/agent/worker/Dockerfile"
  "v012-agent-api    .                                 core/agent/services/agent-api/Dockerfile"
  "v012-meeting-api  .                                 core/meetings/services/meeting-api/Dockerfile"
  "v012-gateway      core/gateway/services/gateway     core/gateway/services/gateway/Dockerfile"
  "v012-mcp          core/meetings/services/mcp        core/meetings/services/mcp/Dockerfile"
  "v012-terminal     clients/terminal                  clients/terminal/Dockerfile"
)

echo "=== release set $TAG @ ${SHA:0:12} ==="

for entry in "${SERVICES[@]}"; do
  read -r name ctx df <<<"$entry"
  echo "--- vexaai/$name:$TAG"
  docker buildx build --builder $BUILDER --platform linux/amd64 \
    "${LABELS[@]}" -f "$df" -t "vexaai/$name:$TAG" --push "$ctx"
done

echo "--- vexaai/vexa-bot:$TAG (base: vexaai/meet-join-env:dev → local alias vexa/meet-join-env:dev)"
docker pull --platform linux/amd64 -q vexaai/meet-join-env:dev
docker tag vexaai/meet-join-env:dev vexa/meet-join-env:dev
docker build --platform linux/amd64 "${LABELS[@]}" \
  -t "vexaai/vexa-bot:$TAG" -f core/meetings/services/bot/Dockerfile .
docker push -q "vexaai/vexa-bot:$TAG"

echo "--- vexaai/vexa-lite:$TAG (multi-arch)"
docker buildx build --builder $BUILDER --platform linux/amd64,linux/arm64 \
  "${LABELS[@]}" -f deploy/lite/Dockerfile.lite -t "vexaai/vexa-lite:$TAG" --push .

echo "=== digests ==="
for img in vexa-lite v012-admin-api v012-runtime v012-agent-worker v012-agent-api \
           v012-meeting-api v012-gateway v012-mcp v012-terminal vexa-bot; do
  d=$(docker buildx imagetools inspect "vexaai/$img:$TAG" 2>/dev/null | awk '/^Digest:/{print $2; exit}')
  echo "vexaai/$img:$TAG  $d"
done
echo "=== build-set complete: $TAG @ ${SHA:0:12} ==="
echo "Next: deploy/release/verify-set.sh $TAG $SHA   (the gate the Release publish requires)"
