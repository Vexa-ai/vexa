#!/usr/bin/env bash
# v0.10.6.1-arm64-manifest — vexa-lite image manifest includes linux/arm64.
#
# Steps:
#   arm64_in_manifest   — Static-grep:
#                           * .github/workflows/docker-publish-multiarch.yml exists
#                           * builds for linux/amd64,linux/arm64
#                           * has the manifest-verification step
#                         Runtime (if VEXA_LITE_IMAGE is set, e.g. compose
#                         smoke after publish):
#                           * docker buildx imagetools inspect $VEXA_LITE_IMAGE
#                             outputs a `linux/arm64` line.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
WORKFLOW="$ROOT_DIR/.github/workflows/docker-publish-multiarch.yml"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-arm64-manifest :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-arm64-manifest-$step"

case "$step" in
  arm64_in_manifest)
    failed=0

    # Static checks (always run) ─────────────────────────────────────
    if [[ ! -f "$WORKFLOW" ]]; then
      echo "    FAIL: workflow file missing: $WORKFLOW"
      failed=1
    else
      if ! grep -q 'platforms: linux/amd64,linux/arm64' "$WORKFLOW"; then
        echo "    FAIL: workflow does not declare linux/arm64 in platforms"
        failed=1
      fi
      if ! grep -q 'imagetools inspect' "$WORKFLOW"; then
        echo "    FAIL: workflow lacks the post-push manifest verification step"
        failed=1
      fi
      if ! grep -q "if ! grep -q 'linux/arm64'" "$WORKFLOW"; then
        echo "    FAIL: workflow's verification step does not assert arm64 in the manifest"
        failed=1
      fi
    fi

    # Runtime check (optional — needs network + pushed image) ────────
    if [[ -n "${VEXA_LITE_IMAGE:-}" ]] && command -v docker >/dev/null 2>&1; then
      echo "    runtime check: VEXA_LITE_IMAGE=$VEXA_LITE_IMAGE"
      if docker buildx imagetools inspect "$VEXA_LITE_IMAGE" 2>/tmp/manifest-err | grep -q 'linux/arm64'; then
        echo "    runtime ok: manifest includes linux/arm64"
      else
        echo "    FAIL: $VEXA_LITE_IMAGE manifest does not include linux/arm64"
        cat /tmp/manifest-err 2>/dev/null | head -5
        failed=1
      fi
    fi

    if (( failed == 0 )); then
      step_pass DOCKER_IMAGES_MANIFEST_INCLUDES_ARM64 "workflow declares + verifies arm64; runtime manifest check OK if image was provided"
    else
      step_fail DOCKER_IMAGES_MANIFEST_INCLUDES_ARM64 "one or more checks failed (see above)"
    fi
    ;;
  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
