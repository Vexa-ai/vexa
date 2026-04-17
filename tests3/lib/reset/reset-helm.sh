#!/usr/bin/env bash
# Fresh reset for a helm/LKE deployment — uninstalls the helm release, deletes
# PVCs (so Postgres starts empty), reinstalls the chart.
#
# Runs locally (uses kubectl + helm from the host). Assumes .state-helm contains
# helm_release, helm_namespace, lke_kubeconfig_path.
set -euo pipefail

: "${STATE:=$(git rev-parse --show-toplevel)/tests3/.state-helm}"

RELEASE=$(cat "$STATE/helm_release" 2>/dev/null || echo "")
NAMESPACE=$(cat "$STATE/helm_namespace" 2>/dev/null || echo "default")
KUBECONFIG_PATH=$(cat "$STATE/lke_kubeconfig_path" 2>/dev/null || echo "")

if [ -z "$RELEASE" ] || [ -z "$KUBECONFIG_PATH" ]; then
    echo "  [reset-helm] missing helm_release or lke_kubeconfig_path in $STATE — skip"
    exit 0
fi

export KUBECONFIG="$KUBECONFIG_PATH"

echo "  [reset-helm] helm uninstall $RELEASE -n $NAMESPACE"
helm uninstall "$RELEASE" -n "$NAMESPACE" 2>&1 | tail -5 || true

echo "  [reset-helm] deleting PVCs for $RELEASE (then unblocking their PVs)"
# Capture the PV names FROM the PVCs before deleting (so we can release the
# underlying Linode block volumes too — storage class uses "retain" policy).
PV_NAMES=$(kubectl get pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=$RELEASE" \
    -o jsonpath='{.items[*].spec.volumeName}' 2>/dev/null || echo "")

kubectl delete pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=$RELEASE" --wait=false 2>&1 || true

# Force-delete the PVs (retain policy leaves them as Released which consumes
# Linode block-storage quota — we've exhausted account limits before).
for pv in $PV_NAMES; do
    [ -z "$pv" ] && continue
    kubectl patch pv "$pv" -p '{"metadata":{"finalizers":null}}' 2>/dev/null || true
    kubectl delete pv "$pv" --wait=false 2>/dev/null || true
done

# Also delete any Linode block volumes that became unattached (belt-and-suspenders).
# Requires linode-cli to be configured on the host running this script.
if command -v linode-cli >/dev/null 2>&1; then
    linode-cli volumes list --json 2>/dev/null \
        | python3 -c "
import json, sys
for v in json.load(sys.stdin):
    if not v.get('linode_id') and str(v.get('label','')).startswith('pvc-'):
        print(v['id'])
" 2>/dev/null \
        | while read vid; do
            linode-cli volumes delete "$vid" 2>&1 | head -1 || true
          done
fi

# Wait up to 30s for pods to finish terminating
for i in $(seq 1 15); do
    COUNT=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=$RELEASE" --no-headers 2>/dev/null | wc -l)
    [ "$COUNT" = "0" ] && break
    sleep 2
done

# Reinstall via the same script that did it initially
T3="$(git rev-parse --show-toplevel)/tests3"
echo "  [reset-helm] reinstalling via lke-setup-helm.sh"
STATE="$STATE" bash "$T3/lib/lke-setup-helm.sh" 2>&1 | tail -10

echo "  [reset-helm] waiting for deployments to be ready..."
for i in $(seq 1 60); do
    NOT_READY=$(kubectl get deploy -n "$NAMESPACE" -l "app.kubernetes.io/instance=$RELEASE" \
        -o jsonpath='{range .items[*]}{.metadata.name}={.status.readyReplicas}/{.status.replicas} {end}' 2>/dev/null \
        | tr ' ' '\n' | awk -F= '$2 != "" && $2 !~ /^0\// && $2 !~ /^\// { sub(/\/.*/, "", $2); if ($2+0 >= 1) c++ } END { print NR - c+0 }')
    [ "${NOT_READY:-999}" = "0" ] && break
    sleep 5
done
