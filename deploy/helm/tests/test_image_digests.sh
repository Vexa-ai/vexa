#!/usr/bin/env bash
set -euo pipefail

HELM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CHART="$HELM_DIR/charts/vexa"
FIXTURE="$HELM_DIR/tests/fixtures/values-image-digests.yaml"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/vexa-image-digests.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

if ! command -v helm >/dev/null 2>&1; then
  echo "SKIP: helm not installed"
  exit 0
fi

fail() { echo "FAIL: $*" >&2; exit 1; }
expect_render_failure() {
  local label="$1"; shift
  if helm template vexa "$CHART" -n vexa "$@" >/dev/null 2>&1; then
    fail "$label rendered successfully"
  fi
  echo "  OK: $label fails closed"
}

echo "=== gate:helm-image-digests ==="

legacy="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml")"
grep -q 'image: "vexaai/v012-gateway:v012"' <<<"$legacy" || fail "legacy tag path changed"
grep -q 'value: "vexaai/vexa-bot:v012"' <<<"$legacy" || fail "legacy spawned bot path changed"
echo "  OK: empty digest preserves legacy tag references"

pinned="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" -f "$FIXTURE")"
refs=()
while IFS= read -r ref; do
  refs+=("$ref")
done < <(awk '/^[[:space:]]+image: / {gsub(/^[[:space:]]+image: /, ""); gsub(/"/, ""); print}' <<<"$pinned")
[[ ${#refs[@]} -eq 13 ]] || fail "expected 13 chart-owned Pod/Job images, got ${#refs[@]}"
for ref in "${refs[@]}"; do
  [[ "$ref" =~ ^[^:@]+([^@]*)?@sha256:[0-9a-f]{64}$ ]] || fail "mutable or malformed rendered image: $ref"
done
for env_name in BROWSER_IMAGE AGENT_IMAGE AGENT_WORKER_IMAGE; do
  ref="$(awk -v name="$env_name" '$0 ~ "name: "name {getline; sub(/^[[:space:]]+value: /, ""); gsub(/\"/, ""); print; exit}' <<<"$pinned")"
  [[ "$ref" =~ @sha256:[0-9a-f]{64}$ ]] || fail "$env_name is not immutable: $ref"
done
echo "  OK: all rendered and spawned runtime images use repository@sha256"

cp -R "$CHART" "$TMP/chart"
sealed_revision=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
printf '%s\n' "$sealed_revision" > "$TMP/chart/OSS-SOURCE-REVISION"
helm template vexa "$TMP/chart" -n vexa -f "$TMP/chart/values-test.yaml" \
  --set sourceRevision="$sealed_revision" >/dev/null
if helm template vexa "$TMP/chart" -n vexa -f "$TMP/chart/values-test.yaml" \
  --set sourceRevision=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb >/dev/null 2>&1; then
  fail "wrong full-length OSS source revision rendered"
fi
echo "  OK: advertised OSS revision is bound to packaged source"

expect_render_failure "truncated digest" --set gateway.image.tag= --set gateway.image.digest=sha256:abc
expect_render_failure "uppercase digest" --set gateway.image.tag= --set gateway.image.digest=sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
expect_render_failure "wrong digest algorithm" --set gateway.image.tag= --set gateway.image.digest=sha512:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
expect_render_failure "component digest plus component tag" --set gateway.image.digest=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
expect_render_failure "component digest plus global tag" --set gateway.image.tag= --set gateway.image.digest=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --set global.imageTag=mutable
expect_render_failure "scalar digest plus legacy tag" --set redis.imageDigest=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --set redis.imageRepository=redis
expect_render_failure "scalar repository without digest" --set redis.imageRepository=redis-custom
expect_render_failure "spawned digest plus legacy tag" --set runtime.browserImageDigest=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --set runtime.browserImageRepository=vexaai/vexa-bot --set runtime.browserImage=mutable:tag
expect_render_failure "spawned digest plus global tag" --set runtime.browserImageDigest=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --set runtime.browserImageRepository=vexaai/vexa-bot --set runtime.browserImage= --set global.imageTag=mutable
expect_render_failure "spawned repository without digest" --set runtime.browserImageRepository=vexaai/vexa-bot-custom

echo "gate:helm-image-digests PASS"
