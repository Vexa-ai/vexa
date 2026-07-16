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
# model-auth wiring: worker creds ride the dispatch spec env FROM agent-api, so agent-api must
# carry the optional secret refs (values-test leaves auth unset — CI has no creds; render + boot
# must stay green, the env ref is optional:true).
need 1 'key: CLAUDE_CODE_OAUTH_TOKEN' "agent-api CLAUDE_CODE_OAUTH_TOKEN secret ref"
need 2 'key: ANTHROPIC_AUTH_TOKEN'    "ANTHROPIC_AUTH_TOKEN secret refs (agent-api + runtime)"
need 2 'name: MEETING_API_URL' "MEETING_API_URL set on gateway AND meeting-api"
# #656: meeting-api MUST get ADMIN_API_URL or calendar sync no-ops and auto-join spawns uncapped.
# It rides the gateway env too; assert >=2 (gateway + meeting-api).
need 2 'name: ADMIN_API_URL'   "ADMIN_API_URL set on gateway AND meeting-api"
# #673: the runtime (backend=k8s) MUST carry its own scheduling constraints as env, or every SPAWNED
# bot/agent Pod (a bare `kubectl run` Pod, not a Deployment child) strands Pending on an all-tainted
# pool and the meeting silently fails. Durable seam-guard so a refactor can't drop it again.
need 1 'name: RUNTIME_K8S_TOLERATIONS'   "runtime carries spawn-Pod tolerations env"
need 1 'name: RUNTIME_K8S_NODE_SELECTOR' "runtime carries spawn-Pod nodeSelector env"

# auth unset (values-test) → the chart Secret must NOT carry the key; auth set → it must.
if printf '%s\n' "$RENDER" | grep -qE '^  CLAUDE_CODE_OAUTH_TOKEN:'; then
  echo "  FAIL: CLAUDE_CODE_OAUTH_TOKEN rendered into the Secret with auth UNSET"; fail=1
else
  echo "  OK: Secret omits CLAUDE_CODE_OAUTH_TOKEN when unset"
fi
RENDER_AUTH="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set secrets.claudeCodeOauthToken=sk-test-oauth)"
if printf '%s\n' "$RENDER_AUTH" | grep -qE '^  CLAUDE_CODE_OAUTH_TOKEN: "sk-test-oauth"'; then
  echo "  OK: CLAUDE_CODE_OAUTH_TOKEN lands in the Secret when set"
else
  echo "  FAIL: CLAUDE_CODE_OAUTH_TOKEN missing from the Secret when set"; fail=1
fi

# #673: with global scheduling set, the runtime env must carry the SERIALIZED JSON values (not just
# the keys) — proof the seam actually threads global.tolerations/nodeSelector to the spawn backend.
RENDER_SCHED="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set-json 'global.tolerations=[{"key":"vexa.ai/pool","operator":"Equal","value":"main","effect":"NoSchedule"}]' \
  --set-json 'global.nodeSelector={"vexa.ai/pool":"main"}')"
# toJson sorts keys, so the toleration serializes as effect,key,operator,value — assert the
# distinctive tokens are present on the value line (order-independent), not the empty "[]".
tol_line="$(printf '%s\n' "$RENDER_SCHED" | grep -A1 'name: RUNTIME_K8S_TOLERATIONS' | grep 'value:')"
if printf '%s\n' "$tol_line" | grep -q 'NoSchedule' && printf '%s\n' "$tol_line" | grep -q 'vexa.ai/pool'; then
  echo "  OK: runtime RUNTIME_K8S_TOLERATIONS carries global.tolerations JSON"
else
  echo "  FAIL: runtime RUNTIME_K8S_TOLERATIONS missing the global.tolerations JSON"; fail=1
fi
sel_line="$(printf '%s\n' "$RENDER_SCHED" | grep -A1 'name: RUNTIME_K8S_NODE_SELECTOR' | grep 'value:')"
if printf '%s\n' "$sel_line" | grep -q 'vexa.ai/pool' && printf '%s\n' "$sel_line" | grep -q 'main'; then
  echo "  OK: runtime RUNTIME_K8S_NODE_SELECTOR carries global.nodeSelector JSON"
else
  echo "  FAIL: runtime RUNTIME_K8S_NODE_SELECTOR missing the global.nodeSelector JSON"; fail=1
fi

[ "$fail" -eq 0 ] && { echo "gate:helm PASS"; exit 0; } || { echo "gate:helm FAIL"; exit 1; }
