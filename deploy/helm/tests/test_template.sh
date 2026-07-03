#!/usr/bin/env bash
# Render the v0.12 vexa chart (no cluster required) and assert the carved control plane is present:
# 5 service Deployments, postgres + minio StatefulSets, redis, minio-init Job, runtime SA/Role/
# RoleBinding (k8s backend), agent-workspaces PVC. This is the gate:helm static proof.
set -euo pipefail

HELM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CHART="$HELM_DIR/charts/vexa"

if ! command -v helm >/dev/null 2>&1; then
  echo "SKIP: helm not installed"; exit 0
fi

RENDER="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml")"

fail=0
need() {  # need <count> <grep-pattern> <label>
  local want="$1" pat="$2" label="$3" got
  got="$(printf '%s\n' "$RENDER" | grep -cE "$pat" || true)"
  if [ "$got" -ge "$want" ]; then echo "  OK: $label ($got)"; else echo "  FAIL: $label — want >=$want got $got"; fail=1; fi
}

echo "=== gate:helm — template render assertions ==="
# 6 long-running services (+ terminal) + redis = 7 Deployments
need 7 '^kind: Deployment'    "Deployments"
need 2 '^kind: StatefulSet'   "StatefulSets (postgres+minio)"
need 9 '^kind: Service$'      "Services"
need 1 'name: vexa-vexa-terminal' "terminal present"
need 1 '^kind: ServiceAccount' "runtime ServiceAccount"
need 1 '^kind: Role$'         "runtime Role"
need 1 '^kind: RoleBinding'   "runtime RoleBinding"
need 1 '^kind: Job'           "minio-init Job"
need 2 '^kind: PersistentVolumeClaim' "PVCs (redis+workspaces)"
need 1 'name: vexa-vexa-agent-api' "agent-api present"
need 1 'RUNTIME_BACKEND'      "runtime backend env"
need 1 'serviceAccountName: vexa-vexa-runtime' "runtime SA bound"

[ "$fail" -eq 0 ] && { echo "gate:helm PASS"; exit 0; } || { echo "gate:helm FAIL"; exit 1; }
