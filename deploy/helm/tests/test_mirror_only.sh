#!/usr/bin/env bash
# gate:helm-mirror (#1006) — the mirror-only render invariant.
#
# A private-registry cluster denies public-registry egress. The chart must therefore be able to
# render with EVERY image it can cause the cluster to pull pointed at one internal registry, with
# no undeclared public fallback anywhere — including the two surfaces that are easy to miss:
#
#   • helm HOOK manifests (the post-install minio-init Job), which never show up in `kubectl get
#     deploy` and used to hardcode `minio/mc:latest`;
#   • RUNTIME-SPAWNED workloads (bot / agent / agent-worker), which are not containers in any
#     rendered manifest at all — they are references handed to the runtime as env, and the runtime
#     creates those Pods later. Long-running Deployments alone are NOT sufficient coverage.
#
# Every assertion below carries its own negative control: the fixture is perturbed to reintroduce
# the exact failure and the check must go RED, then the unperturbed fixture must be GREEN. A test
# that cannot be made to fail proves nothing.
#
# No cluster required.
set -uo pipefail

HELM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CHART="$HELM_DIR/charts/vexa"
FIXTURE="$HELM_DIR/tests/values-mirror-only.yaml"
MIRROR="registry.internal.invalid/"

if ! command -v helm >/dev/null 2>&1; then
  echo "SKIP: helm not installed"; exit 0
fi

fail=0
ok()   { echo "  OK: $*"; }
bad()  { echo "  FAIL: $*"; fail=1; }

render() { helm template vexa "$CHART" -n vexa -f "$FIXTURE" "$@" 2>&1; }

# --- the inventory ----------------------------------------------------------------------------
# Two classes, collected separately because they are two different mistakes:
#   container images   → every `image:` field in every rendered manifest (containers AND
#                        initContainers — the field name is the same, so both are caught).
#   spawn references   → the runtime Deployment's *_IMAGE env values.
container_images() {
  grep -hoE '^[[:space:]]+image:[[:space:]]*"?[^"[:space:]]+' <<<"$1" \
    | sed -E 's/^[[:space:]]*image:[[:space:]]*"?//'
}
spawn_refs() {
  awk '/- name: (BROWSER_IMAGE|AGENT_IMAGE|AGENT_WORKER_IMAGE)$/{n=$3; getline; sub(/^[[:space:]]*value:[[:space:]]*"?/,""); sub(/"$/,""); print n"="$0}' <<<"$1"
}

echo "=== gate:helm-mirror — mirror-only render invariant (#1006) ==="

RENDER="$(render)"
if [ $? -ne 0 ]; then echo "  FAIL: mirror-only fixture did not render:"; echo "$RENDER"; exit 1; fi

# ---------------------------------------------------------------------------------------------
# A3 — every container image and every spawn reference belongs to the configured mirror.
# ---------------------------------------------------------------------------------------------
check_all_mirrored() {  # <render> → prints offenders, returns 1 if any
  local r="$1" offenders=""
  local ref
  while IFS= read -r ref; do
    [ -z "$ref" ] && continue
    case "$ref" in "$MIRROR"*) ;; *) offenders="$offenders container:$ref " ;; esac
  done < <(container_images "$r")
  while IFS= read -r ref; do
    [ -z "$ref" ] && continue
    case "${ref#*=}" in "$MIRROR"*) ;; *) offenders="$offenders spawn:$ref " ;; esac
  done < <(spawn_refs "$r")
  [ -z "$offenders" ] && return 0
  printf '%s\n' "$offenders"; return 1
}

n_container="$(container_images "$RENDER" | grep -c . || true)"
n_spawn="$(spawn_refs "$RENDER" | grep -c . || true)"
if [ "$n_container" -lt 10 ] || [ "$n_spawn" -ne 3 ]; then
  bad "inventory looks wrong — $n_container container images (want >=10), $n_spawn spawn refs (want 3). A shrinking inventory silently narrows this gate."
else
  ok "inventoried $n_container container images + $n_spawn runtime-spawn references"
fi

if offenders="$(check_all_mirrored "$RENDER")"; then
  ok "A3 · every container image and spawn reference is on the configured mirror"
else
  bad "A3 · references outside the mirror: $offenders"
fi

# A3 negative control — reintroduce ONE undeclared public reference; the invariant must go red.
for probe in \
  "--set minio.mc.image.repository=minio/mc --set minio.mc.digest= --set minio.mc.image.tag=latest" \
  "--set runtime.agentWorkerImageRepository=vexaai/v012-agent-worker --set runtime.agentWorkerImageDigest=" \
  "--set runtime.agentImage=vexaai/v012-agent-api:v012"
do
  # shellcheck disable=SC2086
  PROBE_RENDER="$(render $probe)"
  if check_all_mirrored "$PROBE_RENDER" >/dev/null; then
    bad "A3 negative control did NOT go red for [$probe] — the invariant cannot see this class of leak"
  else
    ok "A3 negative control red as required for [$probe]"
  fi
done

# ---------------------------------------------------------------------------------------------
# A1 — the three explicit spawn identities survive a global tag. This is the reported bug.
# ---------------------------------------------------------------------------------------------
BOT_WANT='registry.internal.invalid/vexa/vexa-bot@sha256:1111111111111111111111111111111111111111111111111111111111111111'
AGENT_WANT='registry.internal.invalid/vexa/v012-agent-api:mirrored-2222'
WORKER_WANT='registry.internal.invalid/vexa/v012-agent-worker@sha256:3333333333333333333333333333333333333333333333333333333333333333'
GLOBAL_TAG="$(grep -E '^\s+imageTag:' "$FIXTURE" | head -1 | sed -E 's/.*: *"?([^"]*)"?.*/\1/')"

check_spawn() {  # <render> <env-name> <exact-wanted-ref>
  local got
  got="$(spawn_refs "$1" | grep "^$2=" | cut -d= -f2-)"
  [ "$got" = "$3" ] && return 0
  echo "$2 = ${got:-<absent>} (want $3)"; return 1
}
for pair in "BROWSER_IMAGE:$BOT_WANT" "AGENT_IMAGE:$AGENT_WANT" "AGENT_WORKER_IMAGE:$WORKER_WANT"; do
  env_name="${pair%%:*}"; want="${pair#*:}"
  if msg="$(check_spawn "$RENDER" "$env_name" "$want")"; then
    ok "A1 · $env_name is the exact configured reference, with global.imageTag=$GLOBAL_TAG set"
  else
    bad "A1 · $msg"
  fi
done
# A1 also means the global tag did not leak into any spawn reference.
if spawn_refs "$RENDER" | grep -q ":${GLOBAL_TAG}\$"; then
  bad "A1 · a spawn reference carries global.imageTag ($GLOBAL_TAG) — the explicit reference was rewritten"
else
  ok "A1 · global.imageTag rewrote none of the three spawn references"
fi

# ---------------------------------------------------------------------------------------------
# A2 — the minio-init hook Job renders the exact configured reference.
# ---------------------------------------------------------------------------------------------
MC_WANT='registry.internal.invalid/mirror/mc@sha256:4444444444444444444444444444444444444444444444444444444444444444'
MC_GOT="$(helm template vexa "$CHART" -n vexa -f "$FIXTURE" --show-only templates/job-minio-init.yaml 2>&1 \
  | grep -E '^\s+image:' | sed -E 's/^\s*image:\s*"?//; s/"$//')"
if [ "$MC_GOT" = "$MC_WANT" ]; then
  ok "A2 · minio-init Job uses the configured digest ($MC_GOT)"
else
  bad "A2 · minio-init Job image is '$MC_GOT', want '$MC_WANT'"
fi
if [ "$MC_GOT" = "minio/mc:latest" ]; then
  bad "A2 · minio-init Job still renders the pre-#1006 hardcoded public reference"
fi

# ---------------------------------------------------------------------------------------------
# A4 — the legacy composed path stays green; ambiguous or malformed identities fail CLOSED.
# ---------------------------------------------------------------------------------------------
# Legacy: stock values (no explicit reference, no digest anywhere) must still render the historical
# Docker Hub references, and global.imageTag must still drive all three spawn refs.
LEGACY="$(helm template vexa "$CHART" -n vexa 2>&1)"
for want in "vexaai/vexa-bot:v012" "vexaai/v012-agent-api:v012" "vexaai/v012-agent-worker:v012"; do
  if grep -q "value: \"$want\"" <<<"$LEGACY"; then
    ok "A4 · legacy default renders $want unchanged"
  else
    bad "A4 · legacy default lost $want — backward compatibility broken"
  fi
done
LEGACY_TAGGED="$(helm template vexa "$CHART" -n vexa --set global.imageTag=vLEGACY 2>&1)"
n_tagged="$(spawn_refs "$LEGACY_TAGGED" | grep -c ':vLEGACY$' || true)"
if [ "$n_tagged" -eq 3 ]; then
  ok "A4 · global.imageTag still supplies the tag for all 3 spawn refs when none is explicit"
else
  bad "A4 · global.imageTag drove $n_tagged/3 spawn refs on stock values"
fi

# Fail-closed: each of these must make `helm template` EXIT NON-ZERO. A render that succeeds here
# is the dangerous outcome — the operator believes they pinned bytes and did not.
must_fail() {  # <label> <expected-substring> <helm args…>
  local label="$1" expect="$2"; shift 2
  local out rc
  out="$(helm template vexa "$CHART" -n vexa -f "$FIXTURE" "$@" 2>&1)"; rc=$?
  if [ $rc -eq 0 ]; then
    bad "A4 · $label RENDERED instead of failing closed"
  elif grep -q "$expect" <<<"$out"; then
    ok "A4 · $label fails closed"
  else
    bad "A4 · $label failed, but not with the expected message (want '$expect'): $(head -3 <<<"$out")"
  fi
}
must_fail "reference + digest on the same image (ambiguous)" "an explicit full reference" \
  --set runtime.agentImageDigest=sha256:5555555555555555555555555555555555555555555555555555555555555555
must_fail "truncated digest" "Expected exactly sha256" --set minio.mc.digest=sha256:abc123
must_fail "uppercase digest" "Expected exactly sha256" \
  --set minio.mc.digest=sha256:AAAA111111111111111111111111111111111111111111111111111111111111
must_fail "non-sha256 digest" "Expected exactly sha256" \
  --set minio.mc.digest=sha512:1111111111111111111111111111111111111111111111111111111111111111
must_fail "empty repository with no reference or digest" "empty repository" \
  --set runtime.agentWorkerImageDigest= --set runtime.agentWorkerImageRepository=
must_fail "empty tag with no reference or digest" "empty tag" \
  --set runtime.agentWorkerImageDigest= --set global.imageTag= --set runtime.agentWorkerImageTag=

[ "$fail" -eq 0 ] && { echo "gate:helm-mirror PASS"; exit 0; } || { echo "gate:helm-mirror FAIL"; exit 1; }
