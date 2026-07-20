#!/usr/bin/env bash
# =============================================================================
# deploy/helm/probe.sh — `make probe SURFACE=helm` (k3s / LKE).
#
# The helm surface's wrapper around the shared full-journey probe
# (scripts/probe/journey.sh). Two front-door strategies:
#   • GATEWAY_URL set (a NodePort / ingress / LB) → drive it directly — the
#     same path the release-validate k3s leg exercises.
#   • GATEWAY_URL unset → kubectl port-forward the gateway (and admin-api, to
#     mint a key) for the probe's lifetime.
# ADMIN_TOKEN falls back to the release secret's ADMIN_API_TOKEN.
#
# Overrides: NAMESPACE (vexa) · RELEASE (vexa) · GATEWAY_URL · VEXA_API_KEY ·
#            ADMIN_TOKEN · PROBE_MODE · PROBE_* (see journey.sh)
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

NS="${NAMESPACE:-vexa}"
RELEASE="${RELEASE:-vexa}"
GW_SVC="$RELEASE-gateway"
ADMIN_SVC="$RELEASE-admin-api"
PF_PIDS=()
cleanup() { for p in "${PF_PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done; }
trap cleanup EXIT

kubectl get ns "$NS" >/dev/null

pf() { # svc local_port → port-forward in background, wait until the local port answers TCP
  local svc="$1" lport="$2" rport
  rport="$(kubectl -n "$NS" get svc "$svc" -o jsonpath='{.spec.ports[0].port}')"
  kubectl -n "$NS" port-forward "svc/$svc" "$lport:$rport" >/dev/null 2>&1 &
  PF_PIDS+=($!)
  for i in $(seq 1 20); do
    (exec 3<>"/dev/tcp/127.0.0.1/$lport") 2>/dev/null && { exec 3>&- 3<&-; return 0; }
    sleep 1
  done
  echo "probe: port-forward svc/$svc never answered on :$lport" >&2; return 1
}

if [ -z "${GATEWAY_URL:-}" ]; then
  pf "$GW_SVC" 18856
  GATEWAY_URL="http://127.0.0.1:18856"
fi

if [ -z "${VEXA_API_KEY:-}" ]; then
  if [ -z "${ADMIN_TOKEN:-}" ]; then
    ADMIN_TOKEN="$(kubectl -n "$NS" get secret "$RELEASE-secrets" \
      -o jsonpath='{.data.ADMIN_API_TOKEN}' 2>/dev/null | base64 -d || true)"
  fi
  : "${ADMIN_TOKEN:?no VEXA_API_KEY given and no ADMIN_TOKEN (env or $RELEASE-secrets) to mint one with}"
  pf "$ADMIN_SVC" 18857
  VEXA_API_KEY="$(ADMIN_TOKEN="$ADMIN_TOKEN" ADMIN_API_URL="http://127.0.0.1:18857" \
    EMAIL=probe@vexa.ai SCOPES=bot,tx "$ROOT/deploy/compose/bin/provision-token")"
fi

# The one-shot all-component sweep: every deployment's tail + the pod/event state — the
# whole install's failure set in one kubectl read.
PROBE_SWEEP_CMD="
kubectl -n '$NS' get pods -o wide 2>&1;
for d in \$(kubectl -n '$NS' get deploy -o name); do
  echo \"--- \$d (tail 40) ---\"; kubectl -n '$NS' logs \"\$d\" --all-containers --tail=40 2>&1;
done;
echo '--- recent events ---';
kubectl -n '$NS' get events --sort-by=.lastTimestamp 2>&1 | tail -20"

PROBE_MODE="${PROBE_MODE:-real}"
export GATEWAY_URL VEXA_API_KEY PROBE_MODE PROBE_SWEEP_CMD
"$ROOT/scripts/probe/journey.sh"
