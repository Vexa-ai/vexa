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
# #677: agent-api MUST get VEXA_MEETING_API_URL or its live-SSE owner-lookup calls the compose-only
# http://meeting-api:8080 (unresolvable in-cluster) → fail-closed 403 for the meeting's own owner.
# Only agent-api carries the VEXA_-prefixed spelling, so assert exactly 1.
need 1 'name: VEXA_MEETING_API_URL' "agent-api meeting-api URL (owner-scope)"
# #656: meeting-api MUST get ADMIN_API_URL or calendar sync no-ops and auto-join spawns uncapped.
# It rides the gateway env too; assert >=2 (gateway + meeting-api).
need 2 'name: ADMIN_API_URL'   "ADMIN_API_URL set on gateway AND meeting-api"
# #676: terminal MUST get VEXA_INTERNAL_API_SECRET or the admin internal edge is dead
# (bootstrap-admin claim + per-session key mint fail closed). Terminal is the lone consumer of
# this env-var spelling (other services read the same secret key as INTERNAL_API_SECRET), so >=1.
need 1 'name: VEXA_INTERNAL_API_SECRET' "terminal internal-edge secret"
# #673: the runtime (backend=k8s) MUST carry its own scheduling constraints as env, or every SPAWNED
# bot/agent Pod (a bare `kubectl run` Pod, not a Deployment child) strands Pending on an all-tainted
# pool and the meeting silently fails. Durable seam-guard so a refactor can't drop it again.
need 1 'name: RUNTIME_K8S_TOLERATIONS'   "runtime carries spawn-Pod tolerations env"
need 1 'name: RUNTIME_K8S_NODE_SELECTOR' "runtime carries spawn-Pod nodeSelector env"

# auth unset (values-test) → the chart Secret must NOT carry the key; auth set → it must.
if grep -qE '^  CLAUDE_CODE_OAUTH_TOKEN:' <<< "$RENDER"; then
  echo "  FAIL: CLAUDE_CODE_OAUTH_TOKEN rendered into the Secret with auth UNSET"; fail=1
else
  echo "  OK: Secret omits CLAUDE_CODE_OAUTH_TOKEN when unset"
fi
RENDER_AUTH="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set secrets.claudeCodeOauthToken=sk-test-oauth)"
if grep -qE '^  CLAUDE_CODE_OAUTH_TOKEN: "sk-test-oauth"' <<< "$RENDER_AUTH"; then
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
tol_line="$(grep -A1 'name: RUNTIME_K8S_TOLERATIONS' <<< "$RENDER_SCHED" | grep 'value:')"
if grep -q 'NoSchedule' <<< "$tol_line" && grep -q 'vexa.ai/pool' <<< "$tol_line"; then
  echo "  OK: runtime RUNTIME_K8S_TOLERATIONS carries global.tolerations JSON"
else
  echo "  FAIL: runtime RUNTIME_K8S_TOLERATIONS missing the global.tolerations JSON"; fail=1
fi
sel_line="$(grep -A1 'name: RUNTIME_K8S_NODE_SELECTOR' <<< "$RENDER_SCHED" | grep 'value:')"
if grep -q 'vexa.ai/pool' <<< "$sel_line" && grep -q 'main' <<< "$sel_line"; then
  echo "  OK: runtime RUNTIME_K8S_NODE_SELECTOR carries global.nodeSelector JSON"
else
  echo "  FAIL: runtime RUNTIME_K8S_NODE_SELECTOR missing the global.nodeSelector JSON"; fail=1
fi

# #770: pod topology spread. Empty default (values-test sets nothing) must render NOTHING — the
# field is optional, so a no-spread chart is byte-identical to a chart without it (single-node /
# k3s installs keep working). This is the red→green control direction: nothing here, everything
# once a constraint is set.
if grep -qE 'topologySpreadConstraints:' <<< "$RENDER"; then
  echo "  FAIL: topologySpreadConstraints rendered with empty default (should render nothing)"; fail=1
else
  echo "  OK: no topologySpreadConstraints when unset (empty default renders nothing)"
fi
# A global constraint must land on EVERY component Deployment with THAT component's own selector
# injected (labelSelector omitted by the user → chart fills it). 6 Deployments carry the field
# (gateway, admin-api, meeting-api, runtime, agent-api, terminal), each with its own component
# label under matchLabels.
RENDER_TSC="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set-json 'global.topologySpreadConstraints=[{"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]')"
tsc_count="$(grep -cE '^      topologySpreadConstraints:' <<< "$RENDER_TSC" || true)"
if [ "$tsc_count" -ge 6 ]; then
  echo "  OK: global topologySpreadConstraints on all 6 component Deployments ($tsc_count)"
else
  echo "  FAIL: global topologySpreadConstraints — want >=6 Deployments got $tsc_count"; fail=1
fi
# Each component's OWN selector injected — assert the gateway and meeting-api component labels both
# appear inside an injected topology-spread matchLabels (they'd be absent if the selector weren't
# component-specific).
for comp in gateway meeting-api runtime; do
  if grep -A6 'topologySpreadConstraints:' <<< "$RENDER_TSC" | grep -qE "app.kubernetes.io/component: ${comp}\$"; then
    echo "  OK: topology spread injects ${comp}'s own selector"
  else
    echo "  FAIL: topology spread missing injected selector for ${comp}"; fail=1
  fi
done
# Per-component override wins over the global default: gateway asks for a zone key, meeting-api
# keeps the global hostname key.
RENDER_TSC_OV="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set-json 'global.topologySpreadConstraints=[{"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]' \
  --set-json 'gateway.topologySpreadConstraints=[{"maxSkew":2,"topologyKey":"topology.kubernetes.io/zone","whenUnsatisfiable":"DoNotSchedule"}]')"
gw_block="$(awk '/deployment-gateway.yaml/{f=1} f&&/topologySpreadConstraints:/{p=1} p{print} /^---/{if(p)exit}' <<< "$RENDER_TSC_OV")"
if grep -q 'topology.kubernetes.io/zone' <<< "$gw_block" && grep -q 'maxSkew: 2' <<< "$gw_block"; then
  echo "  OK: per-component topologySpreadConstraints override wins on gateway"
else
  echo "  FAIL: gateway per-component topologySpreadConstraints override did not win"; fail=1
fi

# #774: agent-api mounts the single ReadWriteOnce agent-workspaces PVC, so it MUST opt out of the
# shared zero-downtime RollingUpdate (maxSurge:1 deadlocks on Multi-Attach against an RWO volume)
# and render Recreate — same deliberate opt-out redis already carries. Assert the agent-api block
# specifically renders type: Recreate.
agent_strategy="$(awk '/deployment-agent-api.yaml/{f=1} f&&/^spec:/{p=1} p{print} p&&/selector:/{exit}' <<< "$RENDER")"
if grep -q 'type: Recreate' <<< "$agent_strategy"; then
  echo "  OK: agent-api renders Recreate (RWO workspace PVC — no Multi-Attach deadlock)"
else
  echo "  FAIL: agent-api did not render type: Recreate under default RWO workspace"; fail=1
fi
# The other API/UI Deployments keep the shared zero-downtime RollingUpdate — redis + agent-api are
# the only two single-PVC opt-outs, so exactly 5 RollingUpdate blocks remain (gateway, admin-api,
# meeting-api, runtime, terminal).
roll_count="$(grep -cE '^    type: RollingUpdate' <<< "$RENDER" || true)"
if [ "$roll_count" -eq 5 ]; then
  echo "  OK: 5 non-PVC Deployments keep RollingUpdate (agent-api + redis excepted)"
else
  echo "  FAIL: expected 5 RollingUpdate Deployments, got $roll_count"; fail=1
fi
# A ReadWriteMany workspace lifts the single-mount constraint → agent-api takes the shared rolling
# strategy back (conditional is on accessMode, not hardcoded).
RENDER_RWX="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set agentApi.workspaces.accessMode=ReadWriteMany)"
agent_rwx="$(awk '/deployment-agent-api.yaml/{f=1} f&&/^spec:/{p=1} p{print} p&&/selector:/{exit}' <<< "$RENDER_RWX")"
if grep -q 'type: RollingUpdate' <<< "$agent_rwx"; then
  echo "  OK: agent-api takes RollingUpdate when workspace is ReadWriteMany"
else
  echo "  FAIL: agent-api did not take RollingUpdate under ReadWriteMany workspace"; fail=1
fi

# #813 — the deprecated dashboard is a strictly OPT-IN component: absent from the default render
# (the counts above must never silently grow by it), present with its Deployment + Service when
# enabled, and pinned to its OWN tag (never global.imageTag — it is not part of the release set).
if grep -q 'app.kubernetes.io/component: dashboard' <<< "$RENDER"; then
  echo "  FAIL: dashboard rendered in the DEFAULT (disabled) state"; fail=1
else
  echo "  OK: dashboard absent by default (deprecated, opt-in only)"
fi
RENDER_DASH="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set dashboard.enabled=true --set global.imageTag=vSHOULD-NOT-APPLY)"
dash_count="$(grep -c 'app.kubernetes.io/component: dashboard' <<< "$RENDER_DASH" || true)"
if [ "$dash_count" -ge 3 ]; then
  echo "  OK: dashboard.enabled=true renders its Deployment + Service ($dash_count)"
else
  echo "  FAIL: dashboard.enabled=true rendered $dash_count component labels (want >=3)"; fail=1
fi
if grep -q 'image: "vexaai/dashboard:vSHOULD-NOT-APPLY"' <<< "$RENDER_DASH"; then
  echo "  FAIL: dashboard image followed global.imageTag — it must stay on its own pinned tag"; fail=1
else
  echo "  OK: dashboard image ignores global.imageTag (own pinned tag)"
fi

# #900 — the migrations Job must follow global.imageTag (it runs release code against the
# schema; a rolling-v012 image on a pinned deploy is a schema/code skew). Opposite of the
# dashboard: here global.imageTag MUST win over the meetingApi/migrations fallback tag.
MIG_IMG="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set migrations.enabled=true --set global.imageTag=vMIGRATE \
  --show-only templates/job-migrations.yaml | grep -E '^\s+image:')"
if grep -qE ':vMIGRATE"?$' <<< "$MIG_IMG"; then
  echo "  OK: migrations Job honors global.imageTag (pinned release image)"
else
  echo "  FAIL: migrations Job ignored global.imageTag — schema/code skew risk (#900): $MIG_IMG"; fail=1
fi

[ "$fail" -eq 0 ] && { echo "gate:helm PASS"; exit 0; } || { echo "gate:helm FAIL"; exit 1; }
