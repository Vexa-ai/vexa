#!/usr/bin/env bash
# ALLOY: one lifecycle boundary binds Lite source, image, and running container identity.
set -euo pipefail

command="${1:-}"
[[ -n "$command" ]] || { echo "[ALLOY] provenance: expected dev, published, or status" >&2; exit 2; }
shift

format="human"
while (($#)); do
  case "$1" in
    --format)
      [[ $# -ge 2 ]] || { echo "[ALLOY] provenance: --format requires human or json" >&2; exit 2; }
      format="${2:-human}"
      shift 2
      ;;
    *)
      echo "[ALLOY] provenance: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done
case "$format" in
  ""|human) format="human" ;;
  json) ;;
  *) echo "[ALLOY] provenance: --format must be human or json" >&2; exit 2 ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
root="${ROOT:-$(cd "$script_dir/../../.." && pwd -P)}"
lite_dir="$root/deploy/lite"
env_file="${ENV_FILE:-$root/.env}"
source_identity_bin="${SOURCE_IDENTITY_BIN:-$script_dir/source-identity.sh}"
make_bin="${MAKE_BIN:-make}"
docker_bin="${DOCKER_BIN:-docker}"
dockerhub_user="${DOCKERHUB_USER:-vexaai}"
image_name="${IMAGE_NAME:-vexa-lite}"
app_container="${APP_CONTAINER:-vexa-lite}"

load_identity() {
  local prefix="$1" line key value
  local revision="" dirty="" fingerprint=""
  while IFS= read -r line; do
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      SOURCE_REVISION) revision="$value" ;;
      SOURCE_DIRTY) dirty="$value" ;;
      SOURCE_FINGERPRINT) fingerprint="$value" ;;
      *) echo "[ALLOY] provenance: unexpected source identity field: $key" >&2; return 2 ;;
    esac
  done < <(bash "$source_identity_bin" --root "$root" --format env)
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] \
    || { echo "[ALLOY] provenance: invalid source revision" >&2; return 2; }
  [[ "$dirty" == "0" || "$dirty" == "1" ]] \
    || { echo "[ALLOY] provenance: invalid source dirty flag" >&2; return 2; }
  [[ "$fingerprint" =~ ^[0-9a-f]{64}$ ]] \
    || { echo "[ALLOY] provenance: invalid source fingerprint" >&2; return 2; }
  printf -v "${prefix}_REVISION" '%s' "$revision"
  printf -v "${prefix}_DIRTY" '%s' "$dirty"
  printf -v "${prefix}_FINGERPRINT" '%s' "$fingerprint"
}

run_make() {
  "$make_bin" --no-print-directory -C "$lite_dir" "ROOT=$root" "ENV_FILE=$env_file" "$@"
}

image_value() {
  local image="$1" template="$2"
  "$docker_bin" image inspect --format "$template" "$image"
}

container_value() {
  local template="$1"
  "$docker_bin" inspect --format "$template" "$app_container"
}

env_value() {
  local name="$1"
  grep -E "^${name}=" "$env_file" 2>/dev/null \
    | head -1 \
    | cut -d= -f2- \
    | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//'
}

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  printf '%s' "$value"
}

status_command() {
  local running actual_image container_id mode expected_image health expected_id
  local verdict="MATCH" source_revision="" source_dirty="" source_fingerprint=""
  local image_revision="" image_dirty="" image_fingerprint=""

  if ! running="$(container_value '{{.State.Running}}' 2>/dev/null)"; then
    verdict="LEGACY"
    running="false"
    actual_image=""
    container_id=""
    mode=""
    expected_image=""
    health="missing"
  else
    actual_image="$(container_value '{{.Image}}')"
    container_id="$(container_value '{{.Id}}')"
    mode="$(container_value '{{index .Config.Labels "ai.vexa.lite.mode"}}')"
    expected_image="$(container_value '{{index .Config.Labels "ai.vexa.lite.expected-image"}}')"
    health="$(container_value '{{if .State.Health}}{{.State.Health.Status}}{{else if .State.Running}}running{{else}}stopped{{end}}')"

    if [[ "$mode" != "dev" && "$mode" != "published" ]] || [[ -z "$expected_image" ]]; then
      verdict="LEGACY"
    elif ! expected_id="$(image_value "$expected_image" '{{.Id}}' 2>/dev/null)" \
      || [[ "$actual_image" != "$expected_id" ]]; then
      verdict="STALE"
    elif [[ "$mode" == "dev" ]]; then
      load_identity CURRENT
      source_revision="$CURRENT_REVISION"
      source_dirty="$CURRENT_DIRTY"
      source_fingerprint="$CURRENT_FINGERPRINT"
      image_revision="$(image_value "$actual_image" '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
      image_dirty="$(image_value "$actual_image" '{{index .Config.Labels "ai.vexa.source.dirty"}}')"
      image_fingerprint="$(image_value "$actual_image" '{{index .Config.Labels "ai.vexa.source.fingerprint"}}')"
      if [[ "$source_revision" != "$image_revision" \
        || "$source_dirty" != "$image_dirty" \
        || "$source_fingerprint" != "$image_fingerprint" ]]; then
        verdict="STALE"
      fi
    fi

    if [[ "$verdict" == "MATCH" && "$running" != "true" ]]; then
      verdict="UNHEALTHY"
    elif [[ "$verdict" == "MATCH" && "$health" != "healthy" && "$health" != "running" ]]; then
      verdict="UNHEALTHY"
    fi
  fi

  if [[ "$format" == "json" ]]; then
    printf '{"verdict":"%s","mode":"%s","source_revision":"%s","source_dirty":"%s","source_fingerprint":"%s","expected_image":"%s","image_id":"%s","container":"%s","container_id":"%s","health":"%s"}\n' \
      "$(json_escape "$verdict")" \
      "$(json_escape "$mode")" \
      "$(json_escape "$source_revision")" \
      "$(json_escape "$source_dirty")" \
      "$(json_escape "$source_fingerprint")" \
      "$(json_escape "$expected_image")" \
      "$(json_escape "$actual_image")" \
      "$(json_escape "$app_container")" \
      "$(json_escape "$container_id")" \
      "$(json_escape "$health")"
  else
    echo "[ALLOY] Lite provenance: $verdict"
    echo "  mode:      ${mode:-unknown}"
    [[ -n "$source_revision" ]] && echo "  source:    $source_revision dirty=$source_dirty"
    [[ -n "$source_fingerprint" ]] && echo "  fingerprint: $source_fingerprint"
    echo "  expected:  ${expected_image:-unknown}"
    echo "  image ID:  ${actual_image:-unknown}"
    echo "  container: $app_container"
    echo "  container ID: ${container_id:-unknown} health=$health"
  fi

  [[ "$verdict" == "MATCH" ]]
}

dev_command() {
  local tag image_ref image_id image_revision image_dirty image_fingerprint
  load_identity BEFORE
  tag="alloy-dev-${BEFORE_REVISION}-${BEFORE_FINGERPRINT}"
  image_ref="$image_name:$tag"

  echo "[ALLOY] Building $image_ref from $BEFORE_REVISION dirty=$BEFORE_DIRTY"
  run_make build \
    ALLOY_LITE_PROVENANCE=1 \
    "TAG=$tag" \
    "SOURCE_REVISION=$BEFORE_REVISION" \
    "SOURCE_DIRTY=$BEFORE_DIRTY" \
    "SOURCE_FINGERPRINT=$BEFORE_FINGERPRINT"

  load_identity AFTER
  if [[ "$BEFORE_REVISION" != "$AFTER_REVISION" \
    || "$BEFORE_DIRTY" != "$AFTER_DIRTY" \
    || "$BEFORE_FINGERPRINT" != "$AFTER_FINGERPRINT" ]]; then
    echo "[ALLOY] source changed during build; refusing to launch an ambiguous image" >&2
    return 1
  fi

  image_id="$(image_value "$image_ref" '{{.Id}}')"
  image_revision="$(image_value "$image_id" '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  image_dirty="$(image_value "$image_id" '{{index .Config.Labels "ai.vexa.source.dirty"}}')"
  image_fingerprint="$(image_value "$image_id" '{{index .Config.Labels "ai.vexa.source.fingerprint"}}')"
  if [[ "$image_revision" != "$BEFORE_REVISION" \
    || "$image_dirty" != "$BEFORE_DIRTY" \
    || "$image_fingerprint" != "$BEFORE_FINGERPRINT" ]]; then
    echo "[ALLOY] built image labels do not match the source identity" >&2
    return 1
  fi

  run_make up ALLOY_LITE_PROVENANCE=1 "APP_IMAGE=$image_id" LITE_MODE=dev
  run_make init-db
  run_make test
  status_command
}

published_command() {
  local image_tag image_ref repo_digest
  image_tag="${IMAGE_TAG:-$(env_value IMAGE_TAG)}"
  image_tag="${image_tag:-v012}"
  image_ref="$dockerhub_user/$image_name:$image_tag"

  echo "[ALLOY] Pulling published Lite image $image_ref"
  "$docker_bin" pull "$image_ref"
  repo_digest="$(image_value "$image_ref" '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}')"
  [[ -n "$repo_digest" ]] \
    || { echo "[ALLOY] published image has no RepoDigest: $image_ref" >&2; return 1; }

  run_make up ALLOY_LITE_PROVENANCE=1 "APP_IMAGE=$repo_digest" LITE_MODE=published
  run_make init-db
  run_make test
  status_command
}

case "$command" in
  dev) dev_command ;;
  published) published_command ;;
  status) status_command ;;
  *) echo "[ALLOY] provenance: expected dev, published, or status" >&2; exit 2 ;;
esac
