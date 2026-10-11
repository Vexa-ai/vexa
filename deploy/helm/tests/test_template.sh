#!/usr/bin/env bash
# Render the v0.12 vexa chart (no cluster required) and assert the carved control plane is present:
# 5 service Deployments, the postgres StatefulSet, redis, runtime SA/Role/RoleBinding (k8s backend),
# agent-workspaces PVC — and NO object store: recordings go to the operator's S3 (storage.s3), and
# the storage guards refuse a render without it. This is the gate:helm static proof.
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
need 1 '^kind: StatefulSet'   "StatefulSets (postgres)"
need 8 '^kind: Service$'      "Services"
need 1 'name: vexa-vexa-terminal' "terminal present"
need 1 '^kind: ServiceAccount' "runtime ServiceAccount"
need 1 '^kind: Role$'         "runtime Role"
need 1 '^kind: RoleBinding'   "runtime RoleBinding"
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
# A terminating meeting-api must stay alive until EndpointSlice/kube-proxy stops routing its IP.
# Without this drain, deleting co-located replicas produces immediate connection refusals even
# while another replica remains Ready.
need 1 'terminationGracePeriodSeconds: 30' "meeting-api termination budget rendered"
need 1 '^[[:space:]]+- "sleep 5"$' "meeting-api preStop endpoint-drain delay rendered"
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

# Per-class placement for SPAWNED Pods (runtime.workloadScheduling → RUNTIME_K8S_BOT_* and
# RUNTIME_K8S_AGENT_WORKER_*; the runtime validates them at boot). Default: every key rendered and
# empty, so the runtime adds nothing to a Pod. Set: each class's JSON on its own keys only, the
# runtime-wide RUNTIME_K8S_* untouched, and nothing of it on the chart's own Pods.
envval() { grep -A1 -E "name: $1\$" <<< "$2" | grep 'value:' | sed -E 's/^[[:space:]]*value: //'; }
placement_ok=1
for class in BOT AGENT_WORKER; do
  for pair in 'NODE_SELECTOR="{}"' 'TOLERATIONS="[]"' 'PRIORITY_CLASS_NAME=""' 'IMAGE_PULL_SECRETS="[]"'; do
    key="RUNTIME_K8S_${class}_${pair%%=*}"; want="${pair#*=}"
    got="$(envval "$key" "$RENDER")"
    if [ "$got" != "$want" ]; then echo "  FAIL: $key default — want $want got '${got}'"; placement_ok=0; fi
  done
done
[ "$placement_ok" -eq 1 ] && echo "  OK: per-class placement keys render empty by default (8)" || fail=1
RENDER_PLACE="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set-json 'global.nodeSelector={"vexa.ai/pool":"main"}' \
  --set-json 'runtime.workloadScheduling.meetingBot={"nodeSelector":{"vexa.ai/pool":"stealth"},"tolerations":[{"key":"vexa.ai/pool","operator":"Equal","value":"stealth","effect":"NoSchedule"}],"priorityClassName":"vexa-stealth","imagePullSecrets":["regcred"]}' \
  --set runtime.workloadScheduling.agentWorker.priorityClassName=vexa-stealth-worker \
  --set-json 'runtime.workloadScheduling.agentWorker.imagePullSecrets=[{"name":"worker-reg"}]')"
place_ok=1
check_place() {  # check_place <key> <expected value line>
  local got; got="$(envval "$1" "$RENDER_PLACE")"
  if [ "$got" != "$2" ]; then echo "  FAIL: $1 — want $2 got '${got}'"; place_ok=0; fi
}
check_place RUNTIME_K8S_BOT_NODE_SELECTOR '"{\"vexa.ai/pool\":\"stealth\"}"'
check_place RUNTIME_K8S_BOT_TOLERATIONS '"[{\"effect\":\"NoSchedule\",\"key\":\"vexa.ai/pool\",\"operator\":\"Equal\",\"value\":\"stealth\"}]"'
check_place RUNTIME_K8S_BOT_PRIORITY_CLASS_NAME '"vexa-stealth"'
check_place RUNTIME_K8S_BOT_IMAGE_PULL_SECRETS '"[\"regcred\"]"'
check_place RUNTIME_K8S_AGENT_WORKER_NODE_SELECTOR '"{}"'
check_place RUNTIME_K8S_AGENT_WORKER_TOLERATIONS '"[]"'
check_place RUNTIME_K8S_AGENT_WORKER_PRIORITY_CLASS_NAME '"vexa-stealth-worker"'
check_place RUNTIME_K8S_AGENT_WORKER_IMAGE_PULL_SECRETS '"[{\"name\":\"worker-reg\"}]"'
check_place RUNTIME_K8S_NODE_SELECTOR '"{\"vexa.ai/pool\":\"main\"}"'
if grep -qE '^[[:space:]]+priorityClassName: |regcred|worker-reg' <<< "$(grep -vE '^[[:space:]]+value: ' <<< "$RENDER_PLACE")"; then
  echo "  FAIL: per-class placement leaked onto the chart's own Pods"; place_ok=0
fi
[ "$place_ok" -eq 1 ] && echo "  OK: per-class placement reaches its own keys only, as JSON" || fail=1

# Spawned-Pod hardening and identity (RT5-1/6/7): each class keeps the profile's capabilities unless
# the operator sets a list ([] keeps none, the OpenShift setting); broad tolerations stay refused
# unless opted in; the runtime names its release so adoption never crosses releases.
hard_ok=1
check_hard() {  # check_hard <render> <key> <expected value line>
  local got; got="$(envval "$2" "$1")"
  if [ "$got" != "$3" ]; then echo "  FAIL: $2 — want $3 got '${got}'"; hard_ok=0; fi
}
check_hard "$RENDER" RUNTIME_K8S_BOT_CAPABILITIES '""'
check_hard "$RENDER" RUNTIME_K8S_AGENT_WORKER_CAPABILITIES '""'
check_hard "$RENDER" RUNTIME_K8S_ALLOW_BROAD_TOLERATIONS '"false"'
check_hard "$RENDER" RUNTIME_K8S_INSTANCE '"vexa"'
RENDER_HARD="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set-json 'runtime.workloadScheduling.agentWorker.capabilities=[]' \
  --set-json 'runtime.workloadScheduling.meetingBot.capabilities=["KILL"]' \
  --set runtime.workloadScheduling.allowBroadTolerations=true)"
check_hard "$RENDER_HARD" RUNTIME_K8S_AGENT_WORKER_CAPABILITIES '"[]"'
check_hard "$RENDER_HARD" RUNTIME_K8S_BOT_CAPABILITIES '"[\"KILL\"]"'
check_hard "$RENDER_HARD" RUNTIME_K8S_ALLOW_BROAD_TOLERATIONS '"true"'
[ "$hard_ok" -eq 1 ] && echo "  OK: spawned-Pod capabilities, broad-toleration opt-in and the release instance render" || fail=1

# Meeting bots' Chromium sandbox (runtime.botSandbox): bot Pods name the Localhost profile that
# allows user namespaces for their container only, and a DaemonSet installs it on the nodes from
# the runtime image — root only to write the kubelet's directory: no capability, no escalation, a
# read-only root filesystem, no API token. Off: RuntimeDefault, no DaemonSet. Installer off (the
# OpenShift / Security Profiles Operator route): the named path, no DaemonSet. A path that leaves
# the kubelet's seccomp root does not render.
sb_ok=1
check_sb() { local got; got="$(envval "$2" "$1")"; [ "$got" = "$3" ] || { echo "  FAIL: $2 — want $3 got '${got}'"; sb_ok=0; }; }
DS="$(awk 'BEGIN{RS="\n---\n"} /kind: DaemonSet/ && /component: bot-seccomp/' <<< "$RENDER")"
check_sb "$RENDER" RUNTIME_K8S_BOT_SECCOMP_PROFILE '"vexa/seccomp-userns.json"'
check_sb "$RENDER" RUNTIME_K8S_AGENT_WORKER_SECCOMP_PROFILE '""'
for want in 'automountServiceAccountToken: false' 'runAsUser: 0' 'allowPrivilegeEscalation: false' \
            'readOnlyRootFilesystem: true' 'drop: \["ALL"\]' 'path: "/var/lib/kubelet/seccomp"' \
            'type: DirectoryOrCreate' 'cp /app/src/runtime_kernel/seccomp-userns.json' \
            'dest="/host-seccomp/vexa/seccomp-userns.json"' 'image: "vexaai/v012-runtime:'; do
  grep -qE -- "$want" <<< "$DS" || { echo "  FAIL: bot-seccomp DaemonSet lacks: $want"; sb_ok=0; }
done
grep -qE 'privileged: true|hostNetwork|hostPID|add:' <<< "$DS" && { echo "  FAIL: bot-seccomp DaemonSet is privileged"; sb_ok=0; }
RENDER_SB_OFF="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set runtime.botSandbox.enabled=false)"
check_sb "$RENDER_SB_OFF" RUNTIME_K8S_BOT_SECCOMP_PROFILE '""'
grep -q 'component: bot-seccomp' <<< "$RENDER_SB_OFF" && { echo "  FAIL: a DaemonSet with the sandbox off"; sb_ok=0; }
RENDER_SB_SPO="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set runtime.botSandbox.installer.enabled=false --set runtime.botSandbox.localhostProfile=operator/vexa/bot-userns.json)"
check_sb "$RENDER_SB_SPO" RUNTIME_K8S_BOT_SECCOMP_PROFILE '"operator/vexa/bot-userns.json"'
grep -q 'component: bot-seccomp' <<< "$RENDER_SB_SPO" && { echo "  FAIL: a DaemonSet with the installer off"; sb_ok=0; }
grep -q 'component: bot-seccomp' <<< "$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set runtime.backend=docker)" \
  && { echo "  FAIL: a DaemonSet on the docker backend"; sb_ok=0; }
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set runtime.botSandbox.localhostProfile=../etc/x.json >/dev/null 2>&1; then
  echo "  FAIL: a profile path outside the kubelet's seccomp root rendered"; sb_ok=0
fi
[ "$sb_ok" -eq 1 ] && echo "  OK: bot Pods name the user-namespace profile; its installer is unprivileged but root; off and SPO routes render none" || fail=1

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
# the only two single-PVC opt-outs, so exactly 6 RollingUpdate blocks remain (gateway, admin-api,
# meeting-api, runtime, terminal, mcp).
roll_count="$(grep -cE '^    type: RollingUpdate' <<< "$RENDER" || true)"
if [ "$roll_count" -eq 6 ]; then
  echo "  OK: 6 non-PVC Deployments keep RollingUpdate (agent-api + redis excepted)"
else
  echo "  FAIL: expected 6 RollingUpdate Deployments, got $roll_count"; fail=1
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

# F-D4 — the flows tier's Postgres host/port must resolve through the chart's shared
# vexa.dbHostEffective/vexa.dbPortEffective resolvers, same as admin-api/meeting-api/
# job-migrations — NOT a hardcoded in-cluster component name. Before the original fix this
# rendered "...@vexa-vexa-postgres:5432/..." even with postgres.enabled=false and database.host
# set, so the flows tier could never deploy against an external managed Postgres. F-D5 (below)
# moved the DSN itself from a literal Secret string to a startup-composed one (DB_HOST/DB_PORT
# discrete env vars, same shape admin-api uses) — these assertions now read those, not a literal
# DSN string, but the underlying claim is the same one F-D4 made.
FLOWS_EXT="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set flows.enabled=true --set postgres.enabled=false \
  --set database.host=db.example.internal --set database.port=5432 \
  --set postgres.credentialsSecretName=external-pg-creds \
  --set agentApi.enabled=false --set terminal.enabled=false \
  --show-only templates/flows.yaml)"
if grep -A1 'name: DB_HOST' <<< "$FLOWS_EXT" | grep -q 'value: "db.example.internal"' \
  && grep -A1 'name: DB_PORT' <<< "$FLOWS_EXT" | grep -q 'value: "5432"'; then
  echo "  OK: flows tier DB_HOST/DB_PORT honor database.host/port under postgres.enabled=false (#F-D4)"
else
  echo "  FAIL: flows tier DB_HOST/DB_PORT did not resolve to the external database.host/port (#F-D4)"; fail=1
fi
if grep -q -- '-postgres' <<< "$FLOWS_EXT"; then
  echo "  FAIL: flows tier still references an in-cluster '...-postgres' component name under postgres.enabled=false (#F-D4)"; fail=1
else
  echo "  OK: flows tier renders no in-cluster postgres component name under postgres.enabled=false (#F-D4)"
fi
FLOWS_DEFAULT="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set flows.enabled=true --show-only templates/flows.yaml)"
if grep -A1 'name: DB_HOST' <<< "$FLOWS_DEFAULT" | grep -q 'value: "vexa-vexa-postgres"' \
  && grep -A1 'name: DB_PORT' <<< "$FLOWS_DEFAULT" | grep -q 'value: "5432"'; then
  echo "  OK: flows tier DB_HOST/DB_PORT unchanged under the default in-cluster postgres.enabled=true"
else
  echo "  FAIL: flows tier DB_HOST/DB_PORT changed meaning under default postgres.enabled=true"; fail=1
fi

# F-D5 — the flows tier installs on a production chart as the other services do (platform-adoption
# scoping follow-up). Six items, checked against the SAME external/no-agent/no-terminal render
# ($FLOWS_EXT above) unless noted:
#
# (1) DB credentials via the postgres-credentials Secret (secretKeyRef, same three keys admin-api
#     reads: POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB) — never a chart value inlined into the
#     rendered Secret. Before this, .Values.database.user/.Values.database.password (a decorative
#     placeholder under postgres.enabled=false — the real credentials live only in the operator's
#     pre-existing Secret) were baked into a literal DSN string.
if grep -q 'postgres:postgres' <<< "$FLOWS_EXT"; then
  echo "  FAIL: flows tier still inlines a literal database.user/password into the render (#F-D5.1)"; fail=1
else
  echo "  OK: flows tier renders no inline database.user/password literal (#F-D5.1)"
fi
if grep -B2 'key: POSTGRES_USER' <<< "$FLOWS_EXT" | grep -q 'name: "external-pg-creds"' \
  && grep -B2 'key: POSTGRES_PASSWORD' <<< "$FLOWS_EXT" | grep -q 'name: "external-pg-creds"'; then
  echo "  OK: flows tier sources DB_USER/DB_PASSWORD via secretKeyRef against postgres.credentialsSecretName (#F-D5.1)"
else
  echo "  FAIL: flows tier does not source DB_USER/DB_PASSWORD via the resolved credentials Secret (#F-D5.1)"; fail=1
fi

# (2) sslmode threads from database.sslMode into every composed DSN. Flows only — admin-api and
#     meeting-api's own database modules have no DB_SSL_MODE/sslmode reader in current source (only
#     job-migrations sets that env var, and nothing appears to consume it there either), so adding
#     it to their Deployments would not be the one-line, provably-safe change threading it into
#     flows's own DSN query string is.
if grep -A1 'name: DB_SSL_MODE' <<< "$FLOWS_EXT" | grep -q 'value: "disable"' \
  && grep -q 'sslmode=\${DB_SSL_MODE}' <<< "$FLOWS_EXT"; then
  echo "  OK: flows tier threads database.sslMode into its composed DSNs (#F-D5.2)"
else
  echo "  FAIL: flows tier does not thread database.sslMode into its composed DSNs (#F-D5.2)"; fail=1
fi

# (3) the ensure-db bootstrap connection targets the APPLICATION database (database.name, via
#     DB_NAME/POSTGRES_DB secretKeyRef) rather than Postgres's own "postgres" maintenance
#     database — many managed Postgres offerings do not expose that database to the app's own
#     role, but the application database is guaranteed to exist and be reachable (every other
#     service already depends on it). Checked against the default render, where flows.databaseName
#     ("flows") differs from database.name ("vexa") so the initContainer actually renders.
if grep -B2 'key: POSTGRES_DB' <<< "$FLOWS_DEFAULT" | grep -q 'name: "postgres-credentials"'; then
  echo "  OK: ensure-db bootstrap connection targets the application database via POSTGRES_DB secretKeyRef (#F-D5.3)"
else
  echo "  FAIL: ensure-db bootstrap connection does not target the application database (#F-D5.3)"; fail=1
fi

# (4) VEXA_FLOWS_AGENT_API_URL omitted (no agent Service in a no-agents estate) — flows_config.py
#     declares it a `capability` key: unset means "no agent domain", not a URL pointed at nothing.
if grep -q 'VEXA_FLOWS_AGENT_API_URL' <<< "$FLOWS_EXT"; then
  echo "  FAIL: flows tier still renders VEXA_FLOWS_AGENT_API_URL under agentApi.enabled=false (#F-D5.4)"; fail=1
else
  echo "  OK: flows tier omits VEXA_FLOWS_AGENT_API_URL under agentApi.enabled=false (#F-D5.4)"
fi
if grep -q 'VEXA_FLOWS_AGENT_API_URL' <<< "$FLOWS_DEFAULT"; then
  echo "  OK: flows tier still renders VEXA_FLOWS_AGENT_API_URL under the default agentApi.enabled=true (#F-D5.4)"
else
  echo "  FAIL: flows tier lost VEXA_FLOWS_AGENT_API_URL under the default agentApi.enabled=true (#F-D5.4)"; fail=1
fi

# (5) VEXA_UI_URL omitted (a capability: unset = no link) when terminal.enabled=false — before this
#     it always resolved to terminal.publicUrl or the in-cluster Service address regardless, a dead
#     link in every mail a no-terminal deployment sends.
if grep -q 'VEXA_UI_URL' <<< "$FLOWS_EXT"; then
  echo "  FAIL: flows tier still renders VEXA_UI_URL under terminal.enabled=false (#F-D5.5)"; fail=1
else
  echo "  OK: flows tier omits VEXA_UI_URL under terminal.enabled=false (#F-D5.5)"
fi
if grep -q 'VEXA_UI_URL' <<< "$FLOWS_DEFAULT"; then
  echo "  OK: flows tier still renders VEXA_UI_URL under the default terminal.enabled=true (#F-D5.5)"
else
  echo "  FAIL: flows tier lost VEXA_UI_URL under the default terminal.enabled=true (#F-D5.5)"; fail=1
fi

# (6) the ensure-db initContainer tolerates a pre-created database / a role with no CREATEDB: the
#     create is attempted only when the database is absent, and a privilege failure re-checks once
#     more (another replica, or a pre-created database, may already have it) before refusing loudly
#     rather than crash-looping on a raw permission-denied traceback. Static proof only — no live
#     cluster to actually withhold CREATEDB from a role and watch this branch execute.
if grep -q 'does not exist and this role could' <<< "$FLOWS_DEFAULT" \
  && grep -q 'already exists (created by another process)' <<< "$FLOWS_DEFAULT"; then
  echo "  OK: ensure-db initContainer codes the CREATEDB-tolerant retry/refuse path (#F-D5.6)"
else
  echo "  FAIL: ensure-db initContainer is missing the CREATEDB-tolerant retry/refuse path (#F-D5.6)"; fail=1
fi

# flows.databaseName — default "flows" (nothing changes for anyone); set equal to database.name to
# put the flows tables in the application's own database instead (the shape compose already runs —
# its flows tables live in the shared vexa database, no separate CREATE DATABASE at all). Prod
# reason: the db-backup CronJob dumps only the named application database, so a separate "flows"
# database is unbacked-up. When the two names match, the ensure-db initContainer is skipped
# entirely — nothing to create, the application database already exists by definition.
FLOWS_SHARED_DB="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set flows.enabled=true --set flows.databaseName=vexa --show-only templates/flows.yaml)"
if grep -A1 'name: FLOWS_DB_NAME' <<< "$FLOWS_SHARED_DB" | grep -q 'value: "vexa"'; then
  echo "  OK: flows.databaseName=vexa renders FLOWS_DB_NAME=vexa on every composed DSN"
else
  echo "  FAIL: flows.databaseName=vexa did not render FLOWS_DB_NAME=vexa"; fail=1
fi
if grep -q 'ensure-db' <<< "$FLOWS_SHARED_DB"; then
  echo "  FAIL: ensure-db initContainer still renders when flows.databaseName equals database.name (should be a no-op)"; fail=1
else
  echo "  OK: ensure-db initContainer is skipped when flows.databaseName equals database.name"
fi
if grep -q 'ensure-db' <<< "$FLOWS_DEFAULT"; then
  echo "  OK: ensure-db initContainer still renders under the default flows.databaseName (\"flows\" != \"vexa\")"
else
  echo "  FAIL: ensure-db initContainer missing under the default flows.databaseName"; fail=1
fi

# ── the adoption panel (PRD §16.2 item 5) ────────────────────────────────────────────────────
# OFF BY DEFAULT and ON WHEN ASKED FOR — the red→green control direction every opt-in block in
# this chart is held to. The panel is three ConfigMaps and no workload: this chart publishes a
# dashboard and a datasource file, it never deploys Grafana into a customer's cluster.
if grep -q "adoption" <<< "$RENDER"; then
  echo "  FAIL: adoptionPanel renders under the DEFAULT values (must be opt-in)"; fail=1
else
  echo "  OK: adoptionPanel absent from the default render"
fi

PANEL="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set adoptionPanel.enabled=true --show-only templates/adoption-panel.yaml)"
for want in "vexa-vexa-adoption-dashboard" "vexa-vexa-adoption-datasource" \
            "vexa-vexa-adoption-panel-env" "grafana_dashboard" "grafana_datasource" \
            "vexa-adoption-panel" "vexa-app-db" "vexa-flows-db"; do
  if grep -q "$want" <<< "$PANEL"; then
    echo "  OK: adoptionPanel renders $want"
  else
    echo "  FAIL: adoptionPanel enabled but $want is missing"; fail=1
  fi
done

# The panel creates NO workload. A dashboard that quietly added a Deployment to a bank's cluster
# would be a different change than the one that was reviewed.
if grep -qE "^kind: (Deployment|StatefulSet|Job|Service)$" <<< "$PANEL"; then
  echo "  FAIL: adoptionPanel renders a workload — it must be ConfigMaps only"; fail=1
else
  echo "  OK: adoptionPanel is ConfigMaps only (no Deployment/StatefulSet/Job/Service)"
fi

# NO CREDENTIAL IS EVER RENDERED BY THIS CHART. The datasource carries ${ENV} placeholders that
# Grafana expands itself; the password stays in the Secret it already lives in. A rendered
# password would be a plaintext credential inside a ConfigMap — the whole reason for the
# placeholder shape, and the same discipline flows.yaml states for its own DSN.
if grep -qE "password:[[:space:]]*[^\$[:space:]]" <<< "$PANEL"; then
  echo "  FAIL: adoptionPanel rendered a literal password into a ConfigMap"; fail=1
else
  echo "  OK: adoptionPanel renders no literal credential (password stays an \${ENV} placeholder)"
fi

# The datasource follows PgBouncer when PgBouncer is on, like every other consumer in this
# chart. A reporting datasource opening its own direct connections past the pooler is how a
# dashboard becomes an incident.
POOLED="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set adoptionPanel.enabled=true --set pgbouncer.enabled=true \
  --show-only templates/adoption-panel.yaml)"
if grep -A1 "VEXA_DB_HOST" <<< "$POOLED" | grep -q "pgbouncer"; then
  echo "  OK: adoptionPanel datasource host follows pgbouncer when it is enabled"
else
  echo "  FAIL: adoptionPanel datasource bypasses pgbouncer"; fail=1
fi

# ── the flows tier is PUBLISHABLE: {repository, tag}, not a flat string ────────────────────────
# Until 2026-09-03 flows.image was ONE string ("vexaai/v012-flows:dev"). Two consequences, both
# only visible from outside the repo:
#   (a) it was the single component that ignored global.imageTag, so a release-pinned render still
#       pulled a mutable :dev tag for the Minutes product;
#   (b) the channel publisher pins components by merging {<component>: {image: {tag: <ref>}}} over
#       values.yaml. Merging a map over a string REPLACES it — repository vanishes and the pin
#       cannot be expressed at all, which is why the enterprise station gate refused every chart
#       carrying flows as unpinned (S8) no matter what the publisher did.
# These four assertions are the shape check. Against the flat-string chart the first three fail.
FLOWS_PINNED="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set flows.enabled=true --set global.imageTag=vPINNED --show-only templates/flows.yaml)"
flows_pinned_count="$(grep -cE '^\s+image: "vexaai/v012-flows:vPINNED"$' <<< "$FLOWS_PINNED" || true)"
if [ "$flows_pinned_count" -eq 6 ]; then
  echo "  OK: all 6 flows containers follow global.imageTag ($flows_pinned_count)"
else
  echo "  FAIL: flows containers following global.imageTag — want 6 got $flows_pinned_count"; fail=1
fi
# The publisher's exact pin shape: `flows.image.tag` set to "<version>@sha256:<digest>". This is
# the addressability proof — a flat string has no .tag to set.
PIN_REF='v0.12.27@sha256:1111111111111111111111111111111111111111111111111111111111111111'
FLOWS_DIGEST="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set flows.enabled=true --set "flows.image.tag=$PIN_REF" --show-only templates/flows.yaml)"
digest_count="$(grep -cE "^\s+image: \"vexaai/v012-flows:${PIN_REF//./\\.}\"$" <<< "$FLOWS_DIGEST" || true)"
if [ "$digest_count" -eq 6 ]; then
  echo "  OK: publisher pin shape flows.image.tag=<version>@sha256 reaches all 6 containers ($digest_count)"
else
  echo "  FAIL: flows.image.tag digest pin reached $digest_count of 6 containers"; fail=1
fi
# The default (unpinned) render must name a repository and a tag, never a mutable :dev.
if grep -qE '^\s+image: "vexaai/v012-flows:dev"$' <<< "$FLOWS_DEFAULT"; then
  echo "  FAIL: flows default image is still the mutable :dev tag"; fail=1
else
  echo "  OK: flows default image is not :dev"
fi
if grep -qE '^\s+image: "vexaai/v012-flows:v012"$' <<< "$FLOWS_DEFAULT"; then
  echo "  OK: flows default image is repository:tag at the component default (v012)"
else
  echo "  FAIL: flows default image is not vexaai/v012-flows:v012"; fail=1
fi

# ── S6: the ensure-db initContainer is a container and must declare its resources ──────────────
# It declared NONE — 12 findings (4 per flows Deployment: cpu/memory × requests/limits) on the
# enterprise station gate, and the #1005 failure shape in a namespace with a small LimitRange
# default. Assert every one of the three ensure-db blocks carries all four numbers.
init_res=0
for i in 1 2 3; do
  blk="$(awk -v n="$i" '/- name: ensure-db/{c++} c==n{print} c==n && /^      containers:/{exit}' <<< "$FLOWS_DEFAULT")"
  if grep -q 'requests: {cpu: 50m, memory: 96Mi}' <<< "$blk" \
    && grep -q 'limits: {cpu: 250m, memory: 256Mi}' <<< "$blk"; then
    init_res=$((init_res+1))
  fi
done
if [ "$init_res" -eq 3 ]; then
  echo "  OK: all 3 ensure-db initContainers declare cpu+memory requests AND limits (S6)"
else
  echo "  FAIL: only $init_res of 3 ensure-db initContainers declare resources (S6, 12 findings)"; fail=1
fi

# ── 0.12.27 car 2 — A2 (gateway names the agent domain only when it is deployed) and A12 (the
#    flows tier's own container is configured like the two beside it). These assert VALUES, not
#    just key NAMES: every defect below rendered a key with the right name and the wrong content,
#    and the name-only greps above all passed while the tier could not boot.

# A2 — AGENT_API_URL is what puts `agent` in the gateway's present set, and the present set is
# loaded strictly (a named domain with no routes.v1 manifest is a ManifestError at import, i.e. a
# crash-loop). It was rendered unconditionally, so a chart with agentApi.enabled=false named a
# Service that does not exist in that release. BOTH branches asserted.
GW_NO_AGENT="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set agentApi.enabled=false --show-only templates/deployment-gateway.yaml)"
if grep -q 'AGENT_API_URL' <<< "$GW_NO_AGENT"; then
  echo "  FAIL: gateway still names AGENT_API_URL under agentApi.enabled=false — strict manifest load, crash-loop (#A2)"; fail=1
else
  echo "  OK: gateway omits AGENT_API_URL under agentApi.enabled=false (#A2)"
fi
GW_AGENT="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --show-only templates/deployment-gateway.yaml)"
if grep -A1 'name: AGENT_API_URL' <<< "$GW_AGENT" | grep -q 'value: "http://vexa-vexa-agent-api:8100"'; then
  echo "  OK: gateway names the agent-api Service under the default agentApi.enabled=true (#A2)"
else
  echo "  FAIL: gateway does not name the agent-api Service under the default agentApi.enabled=true (#A2)"; fail=1
fi

# A12.1 — VEXA_FLOWS_AGENT_API_URL was rendered as an EMPTY STRING exactly when the agent domain
# WAS enabled, and flows' `_is_set` reads "" as unset: the guard and the value were inverted, so
# every agent step answered not_present on an estate running agent-api. Value-level, both branches.
if grep -A1 'name: VEXA_FLOWS_AGENT_API_URL' <<< "$FLOWS_DEFAULT" | grep -q 'value: "http://vexa-vexa-agent-api:8100"'; then
  echo "  OK: flows names the agent-api Service when agentApi.enabled (not an empty string) (#A12)"
else
  echo "  FAIL: flows renders VEXA_FLOWS_AGENT_API_URL with no Service address — unset by any reader (#A12)"; fail=1
fi

# A12.2 — the flows-api container carries the doors and credentials the worker/mailbox carry.
# VEXA_FLOWS_ADMIN_API_URL is required-explicit in flows' config.v1, so its absence was a boot
# refusal, not a degrade. Asserted on the flows-api container specifically (the render is split at
# the flows-api Deployment so a value on the worker cannot satisfy a claim about the api).
# The flows-api DEPLOYMENT document alone. helm orders a render by install kind, not by source
# order, so "everything after the first line naming flows-api" is the Service, not the Deployment —
# and a claim about the api container would then be satisfied by the worker's env twenty lines
# down. Select the document by its own kind + component label instead.
FLOWS_API_ONLY="$(awk 'BEGIN{RS="\n---\n"} /kind: Deployment/ && /component: flows-api/' <<< "$FLOWS_DEFAULT")"
for kv in \
  'VEXA_FLOWS_ADMIN_API_URL|value: "http://vexa-vexa-admin-api:8001"' \
  'VEXA_FLOWS_GATEWAY_URL|value: "http://vexa-vexa-gateway:8000"' \
  'VEXA_FLOWS_API_HOST|value: "0.0.0.0"' ; do
  k="${kv%%|*}"; v="${kv##*|}"
  if grep -A1 "name: $k" <<< "$FLOWS_API_ONLY" | grep -qF "$v"; then
    echo "  OK: flows-api carries $k = ${v#value: } (#A12)"
  else
    echo "  FAIL: flows-api is missing $k = ${v#value: } (#A12)"; fail=1
  fi
done
if grep -q 'key: ADMIN_API_TOKEN' <<< "$FLOWS_API_ONLY"; then
  echo "  OK: flows-api sources VEXA_FLOWS_ADMIN_KEY from the admin-token Secret (#A12)"
else
  echo "  FAIL: flows-api does not source VEXA_FLOWS_ADMIN_KEY (#A12)"; fail=1
fi

# A12.3 — probes on flows-api (it has a /health; the worker and mailbox have no server at all and
# carry a rendered comment saying so).
if grep -q 'livenessProbe' <<< "$FLOWS_API_ONLY" && grep -q 'readinessProbe' <<< "$FLOWS_API_ONLY"; then
  echo "  OK: flows-api carries liveness + readiness probes on /health (#A12)"
else
  echo "  FAIL: flows-api carries no probes (#A12)"; fail=1
fi

# A12.4 — the flows image follows global.imageTag like every other service instead of a mutable
# `:dev`, and an explicit flows.image still wins (that is how a publisher digest-pins a release).
FLOWS_PINNED="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set flows.enabled=true --set global.imageTag=vPIN --show-only templates/flows.yaml)"
if grep -q 'image: "vexaai/v012-flows:vPIN"' <<< "$FLOWS_PINNED" \
  && ! grep -q 'v012-flows:dev' <<< "$FLOWS_PINNED"; then
  echo "  OK: flows image honors global.imageTag (no mutable :dev left) (#A12)"
else
  echo "  FAIL: flows image ignored global.imageTag (#A12)"; fail=1
fi
FLOWS_OVERRIDE="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set flows.enabled=true --set global.imageTag=vPIN \
  --set flows.image=reg.example/flows@sha256:abc --show-only templates/flows.yaml)"
if grep -q 'image: "reg.example/flows@sha256:abc"' <<< "$FLOWS_OVERRIDE"; then
  echo "  OK: an explicit flows.image still wins over global.imageTag (#A12)"
else
  echo "  FAIL: an explicit flows.image no longer wins over global.imageTag (#A12)"; fail=1
fi

# ── storage.s3 — recordings in the operator's own S3; the built-in MinIO is gone ─────────────────
# The chart runs no object store: no MinIO workload, Service, PVC template, bucket Job or image in
# the render, and no StatefulSet beyond postgres (asserted exactly, not >=).
exact() {  # exact <count> <grep-pattern> <label> [render]
  local want="$1" pat="$2" label="$3" got
  got="$(printf '%s\n' "${4:-$RENDER}" | grep -cE "$pat" || true)"
  if [ "$got" -eq "$want" ]; then echo "  OK: $label ($got)"; else echo "  FAIL: $label — want $want got $got"; fail=1; fi
}
exact 0 'component: minio' "no MinIO workload, Service or Job (component: minio)"
exact 0 'image: .*(minio|/mc[:@])' "no MinIO server or client image"
exact 0 'vexa-vexa-minio' "nothing addresses the old MinIO Service"
exact 0 'minio-init' "no bucket-init Job"
exact 1 '^kind: StatefulSet' "exactly one StatefulSet (postgres)"
exact 0 '^kind: Job' "no Job in the default render (the bucket-init Job is gone)"
# meeting-api's storage env comes from storage.s3, VALUE-level (values-test points at the fixture).
MA="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-meeting-api.yaml)"
for kv in 'S3_ENDPOINT|value: "http://s3-fixture:7070"' 'MINIO_ENDPOINT|value: "http://s3-fixture:7070"' \
          'MINIO_BUCKET|value: "vexa-recordings"' 'MINIO_SECURE|value: "false"' \
          'STORAGE_BACKEND|value: "s3"' 'AWS_CONFIG_FILE|value: /etc/vexa/s3/config'; do
  k="${kv%%|*}"; v="${kv##*|}"
  if grep -A1 "name: $k\$" <<< "$MA" | grep -qF "$v"; then echo "  OK: meeting-api $k = ${v#value: }"
  else echo "  FAIL: meeting-api $k is not ${v#value: }"; fail=1; fi
done
# Credentials only by secretKeyRef on storage.s3.existingSecret — never a literal value. Both name
# pairs (S3_* and the MINIO_* keys the /health object_storage row is declared on) read the Secret.
for k in S3_ACCESS_KEY MINIO_ACCESS_KEY; do
  if grep -A5 "name: $k\$" <<< "$MA" | tr -d '\n' | grep -qE 'secretKeyRef:[[:space:]]+name: "s3-fixture-credentials"[[:space:]]+key: "AWS_ACCESS_KEY_ID"'; then
    echo "  OK: $k from secretKeyRef s3-fixture-credentials/AWS_ACCESS_KEY_ID"
  else echo "  FAIL: $k is not read from the storage.s3 Secret"; fail=1; fi
done
for k in S3_SECRET_KEY MINIO_SECRET_KEY; do
  if grep -A5 "name: $k\$" <<< "$MA" | tr -d '\n' | grep -qE 'secretKeyRef:[[:space:]]+name: "s3-fixture-credentials"[[:space:]]+key: "AWS_SECRET_ACCESS_KEY"'; then
    echo "  OK: $k from secretKeyRef s3-fixture-credentials/AWS_SECRET_ACCESS_KEY"
  else echo "  FAIL: $k is not read from the storage.s3 Secret"; fail=1; fi
done
if grep -A1 -E 'name: (S3|MINIO)_(ACCESS|SECRET)_KEY$' <<< "$MA" | grep -q 'value:'; then
  echo "  FAIL: a storage credential is rendered as a literal value"; fail=1
else echo "  OK: no storage credential rendered as a literal value"; fi
# The boto3 shared-config file: path-style by default, virtual when forcePathStyle=false, and a CA
# bundle only when one is named (ConfigMap or Secret, mounted read-only at the path it names).
CM="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/configmap-s3-client.yaml)"
if grep -q 'addressing_style = path' <<< "$CM" && grep -q 'region = us-east-1' <<< "$CM" && ! grep -q ca_bundle <<< "$CM"; then
  echo "  OK: S3 client config defaults (path-style, us-east-1, system CAs)"
else echo "  FAIL: S3 client config defaults wrong"; fail=1; fi
CMV="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set storage.s3.forcePathStyle=false \
  --set storage.s3.region=eu-central-1 --show-only templates/configmap-s3-client.yaml)"
if grep -q 'addressing_style = virtual' <<< "$CMV" && grep -q 'region = eu-central-1' <<< "$CMV"; then
  echo "  OK: forcePathStyle=false → virtual-hosted; region threads through"
else echo "  FAIL: forcePathStyle=false / region not honored"; fail=1; fi
CA="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set storage.s3.caBundle.configMapName=corp-ca \
  --set storage.s3.caBundle.key=ca-bundle.crt)"
if grep -q 'ca_bundle = /etc/vexa/s3-ca/ca.crt' <<< "$CA" && grep -A6 'name: s3-ca$' <<< "$CA" | grep -q 'name: "corp-ca"' \
   && grep -q 'key: "ca-bundle.crt"' <<< "$CA" && grep -q 'mountPath: /etc/vexa/s3-ca' <<< "$CA"; then
  echo "  OK: caBundle.configMapName mounts the bundle and names it in the client config"
else echo "  FAIL: caBundle.configMapName not wired"; fail=1; fi
CAS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set storage.s3.caBundle.secretName=corp-ca \
  --show-only templates/deployment-meeting-api.yaml)"
if grep -A3 'name: s3-ca$' <<< "$CAS" | grep -q 'secretName: "corp-ca"'; then
  echo "  OK: caBundle.secretName mounts the bundle from a Secret"
else echo "  FAIL: caBundle.secretName not wired"; fail=1; fi
# A pinned MinIO-era values shape with MinIO OFF (the enterprise kit's rehearsal values) renders.
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set minio.enabled=false >/dev/null 2>&1; then
  echo "  OK: minio.enabled=false beside storage.s3 renders (MinIO-off values keep working)"
else echo "  FAIL: minio.enabled=false beside storage.s3 does not render"; fail=1; fi

# Render the Install command's actual --set flags, substituting only its generated secrets and
# bucket placeholder. Parse the flags as data: never execute commands copied from the docs.
INSTALL_SECRET=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
INSTALL_ARGS=()
while read -r flag value; do
  INSTALL_ARGS+=("$flag" "$value")
done < <(awk -v secret="$INSTALL_SECRET" '
  /^helm install vexa / { install=1; next }
  install && /^```/ { exit }
  install {
    sub(/[[:space:]]*\\$/, "")
    gsub(/"\$\(openssl rand -hex 32\)"/, secret)
    sub(/<your bucket>/, "b")
    print
  }
' "$HELM_DIR/../../docs/docs/deployment-kubernetes.mdx")
if INSTALL="$(helm template vexa "$CHART" -n vexa "${INSTALL_ARGS[@]}" 2>&1)" && [ -n "$INSTALL" ]; then
  echo "  OK: documented Install command flags render successfully"
  exact 0 'CHANGE_ME' "documented Install has no published admin token" "$INSTALL"
  exact 4 "^  (ADMIN_API_TOKEN|INTERNAL_API_SECRET|VEXA_DISPATCH_SIGNING_KEY|NEXTAUTH_SECRET): \"$INSTALL_SECRET\"$" \
    "documented Install supplies all four secrets" "$INSTALL"
else
  echo "  FAIL: documented Install command flags did not render: $INSTALL"; fail=1
fi

# Negative controls — each MUST fail to render, with the message that names the fix.
# ── gateway-identity.v1 + the one MCP server (Vexa-ai/vexa#1783) ────────────────────────────────────────
# The gateway signs the identity it resolved with an Ed25519 PRIVATE key that only it mounts;
# agent-api, meeting-api and the credential broker verify with the PUBLIC key and cannot sign.
# identity verifies a worker's delegation token and agent-api signs it.
need 2 'key: VEXA_MCP_DELEGATION_SECRET'   "delegation key on agent-api and admin-api"
exact 0 'VEXA_GATEWAY_IDENTITY_SECRET' "no shared identity secret anywhere (the HMAC key is gone)"
exact 1 'name: VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE$' "the signing-key path is set on one workload"
exact 3 'name: VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE$' "the public-key path on agent-api, meeting-api, credential broker"
# Each Deployment, one document at a time: which mounts the signing Secret, which the public ConfigMap.
identity_mounts() {  # identity_mounts <render> → "<deployment> <signing|public>" per mount
  awk '/^---/{name=""} /^kind: Deployment/{d=1} /^kind: /&&!/Deployment/{d=0}
       d&&/^  name: /&&name==""{name=$2}
       d&&/secretName: vexa-vexa-identity-signing-key$/{print name" signing"}
       d&&/name: vexa-vexa-identity-public-key$/{print name" public"}' <<< "$1" | sort
}
WANT_MOUNTS="vexa-vexa-agent-api public
vexa-vexa-credential-broker public
vexa-vexa-gateway signing
vexa-vexa-meeting-api public"
if [ "$(identity_mounts "$RENDER")" = "$WANT_MOUNTS" ]; then
  echo "  OK: the signing key is mounted by the gateway alone; the public key by the three verifiers"
else
  echo "  FAIL: identity key mounts — got: $(identity_mounts "$RENDER" | tr '\n' ';')"; fail=1
fi
# The pair: a generated Ed25519 private key, and a public key that IS its public half (openssl
# derives it independently of the chart's certificate trick). No key material is printed.
identity_pair_ok() {  # identity_pair_ok <render>
  local priv pub derived tmp
  tmp="$(mktemp -d)"
  priv="$(awk '/^  signing-key.pem: /{print $2}' <<< "$1")"
  pub="$(awk '/^  public-key.pem: \|/{f=1;next} f&&/^    /{sub(/^    /,"");print;next} f{exit}' <<< "$1")"
  [ -n "$priv" ] && [ -n "$pub" ] || { rm -rf "$tmp"; return 1; }
  printf '%s' "$priv" | base64 -d > "$tmp/k" 2>/dev/null || printf '%s' "$priv" | base64 -D > "$tmp/k"
  openssl pkey -in "$tmp/k" -noout -text 2>/dev/null | grep -q 'ED25519 Private-Key' || { rm -rf "$tmp"; return 1; }
  derived="$(openssl pkey -in "$tmp/k" -pubout 2>/dev/null)"
  rm -rf "$tmp"
  [ "$derived" = "$pub" ]
}
if command -v openssl >/dev/null 2>&1; then
  if identity_pair_ok "$RENDER"; then
    echo "  OK: a generated Ed25519 signing key, and the ConfigMap's public key is its public half"
  else
    echo "  FAIL: the identity Secret/ConfigMap are not a matching Ed25519 pair"; fail=1
  fi
  OWN_KEY="$(openssl genpkey -algorithm ed25519 2>/dev/null)"
  OWN="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set-string identity.signingKey="$OWN_KEY")"
  if identity_pair_ok "$OWN" \
     && [ "$(awk '/^  signing-key.pem: /{print $2}' <<< "$OWN")" = "$(printf '%s' "$OWN_KEY" | base64 | tr -d '\n')" ]; then
    echo "  OK: identity.signingKey is used as given and its public half derived"
  else
    echo "  FAIL: identity.signingKey wiring"; fail=1
  fi
  unset OWN_KEY OWN
  NOT_ED="$(openssl genpkey -algorithm ec -pkeyopt ec_paramgen_curve:P-256 2>/dev/null)"
  if ! helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set-string identity.signingKey="$NOT_ED" >/dev/null 2>&1; then
    echo "  OK: a signing key that is not Ed25519 refuses the render"
  else
    echo "  FAIL: a P-256 identity.signingKey rendered"; fail=1
  fi
  unset NOT_ED
else
  echo "  SKIP: openssl not installed — identity key pair not cross-checked"
fi
# ADR-0037: the chart deploys the assembled MCP server, the gateway relays /mcp to it, and every
# agent worker's toolbelt points at the gateway's /mcp.
need 1 'name: vexa-vexa-mcp$' "mcp Service/Deployment rendered"
if grep -A1 'name: MCP_URL$' <<< "$RENDER" | grep -q 'value: "http://vexa-vexa-mcp:8010"'; then
  echo "  OK: the gateway relays /mcp to the chart's MCP service"
else
  echo "  FAIL: gateway MCP_URL does not name vexa-vexa-mcp:8010"; fail=1
fi
if grep -A1 'name: VEXA_MCP_URL$' <<< "$RENDER" | grep -q 'value: "http://vexa-vexa-gateway:8000/mcp"'; then
  echo "  OK: agent-api's worker toolbelt points at the gateway's /mcp"
else
  echo "  FAIL: agent-api VEXA_MCP_URL does not name the gateway's /mcp"; fail=1
fi
if grep -A1 'name: AGENT_API_URL$' <<< "$(awk '/deployment-mcp.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER")" \
    | grep -q 'vexa-vexa-agent-api:8100'; then
  echo "  OK: the MCP service assembles the agent domain's tools"
else
  echo "  FAIL: the MCP service does not name agent-api"; fail=1
fi
# The second wall: only legitimate callers reach agent-api and meeting-api, bots keep their
# callbacks into meeting-api, and agent workers reach neither.
need 1 'name: vexa-vexa-agent-api-ingress$'   "agent-api ingress NetworkPolicy"
need 1 'name: vexa-vexa-meeting-api-ingress$' "meeting-api ingress NetworkPolicy"
np_agent="$(awk '/networkpolicy.yaml/{f=1} f&&/name: vexa-vexa-agent-api-ingress/{p=1} p{print} p&&/^---/{exit}' <<< "$RENDER")"
np_meeting="$(awk '/name: vexa-vexa-meeting-api-ingress/{p=1} p{print} p&&/^---/{exit}' <<< "$RENDER")"
for c in gateway mcp runtime terminal; do
  if grep -q "app.kubernetes.io/component: $c\$" <<< "$np_agent"; then echo "  OK: agent-api admits $c"
  else echo "  FAIL: agent-api NetworkPolicy does not admit $c"; fail=1; fi
done
if grep -q 'runtime.managed' <<< "$np_agent"; then
  echo "  FAIL: agent-api NetworkPolicy admits runtime-managed pods (bots/workers)"; fail=1
else echo "  OK: agent-api admits no bot or worker pod"; fi
if grep -q 'runtime.managed: "true"' <<< "$np_meeting" && grep -A2 'key: vexa.role' <<< "$np_meeting" | grep -q 'NotIn'; then
  echo "  OK: meeting-api admits bots (runtime-managed, not workers) for their callbacks"
else
  echo "  FAIL: meeting-api NetworkPolicy does not admit bot callbacks while excluding workers"; fail=1
fi
# The runtime admits its two callers only; spawned workloads take no inbound connection and reach
# only what their class needs, never a private address outside those rules.
np_rt="$(awk '/name: vexa-vexa-runtime-ingress/{p=1} p{print} p&&/^---/{exit}' <<< "$RENDER")"
np_worker="$(awk '/name: vexa-vexa-worker-egress/{p=1} p{print} p&&/^---/{exit}' <<< "$RENDER")"
np_bot="$(awk '/name: vexa-vexa-bot-egress/{p=1} p{print} p&&/^---/{exit}' <<< "$RENDER")"
np_wl_in="$(awk '/name: vexa-vexa-workloads-ingress/{p=1} p{print} p&&/^---/{exit}' <<< "$RENDER")"
if [ "$(grep -c 'app.kubernetes.io/component:' <<< "$np_rt")" = 3 ] \
    && grep -q 'component: agent-api$' <<< "$np_rt" && grep -q 'component: meeting-api$' <<< "$np_rt" \
    && ! grep -q 'runtime.managed' <<< "$np_rt"; then
  echo "  OK: the runtime admits agent-api and meeting-api only"
else echo "  FAIL: runtime ingress NetworkPolicy is not agent-api + meeting-api only"; fail=1; fi
if grep -q 'runtime.managed: "true"' <<< "$np_wl_in" && ! grep -q '^  ingress:' <<< "$np_wl_in"; then
  echo "  OK: spawned workloads take no inbound connection"
else echo "  FAIL: spawned workloads accept inbound connections"; fail=1; fi
for c in gateway redis; do
  if grep -q "component: $c\$" <<< "$np_worker"; then echo "  OK: workers reach $c"
  else echo "  FAIL: worker egress does not allow $c"; fail=1; fi
done
for c in meeting-api redis; do
  if grep -q "component: $c\$" <<< "$np_bot"; then echo "  OK: bots reach $c"
  else echo "  FAIL: bot egress does not allow $c"; fail=1; fi
done
for pair in "worker:meeting-api" "worker:runtime" "worker:admin-api" "worker:agent-api" "worker:postgres" \
            "bot:gateway" "bot:runtime" "bot:admin-api" "bot:agent-api" "bot:postgres"; do
  cls="${pair%%:*}"; c="${pair#*:}"; body="$np_worker"; [ "$cls" = bot ] && body="$np_bot"
  if grep -q "component: $c\$" <<< "$body"; then echo "  FAIL: $cls egress allows $c"; fail=1
  else echo "  OK: ${cls}s cannot reach $c"; fi
done
for body in "$np_worker" "$np_bot"; do
  if grep -q -- '- 10.0.0.0/8' <<< "$body" && grep -q -- '- 169.254.0.0/16' <<< "$body"; then :
  else echo "  FAIL: workload egress does not exclude the private and metadata ranges"; fail=1; fi
done
echo "  OK: workload internet egress excludes the private and metadata ranges"
np_off="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set networkPolicy.enabled=false)"
if grep -qE 'name: vexa-vexa-(agent-api|meeting-api|runtime|workloads)-ingress|name: vexa-vexa-(worker|bot)-egress' <<< "$np_off"; then
  echo "  FAIL: networkPolicy.enabled=false still renders a policy"; fail=1
else echo "  OK: networkPolicy.enabled=false renders none of the policies"; fi
# The runtime caller credential reaches the runtime and its two callers, nothing else.
holders="$(awk '/^# Source: /{src=$3} /key: RUNTIME_API_TOKEN$/{print src}' <<< "$RENDER" | sort -u | tr '\n' ' ')"
if [ "$holders" = "vexa/templates/deployment-agent-api.yaml vexa/templates/deployment-meeting-api.yaml vexa/templates/deployment-runtime.yaml " ]; then
  echo "  OK: RUNTIME_API_TOKEN reaches the runtime, agent-api and meeting-api only"
else echo "  FAIL: RUNTIME_API_TOKEN holders are: $holders"; fail=1; fi
rt_secret="$(awk '/^  RUNTIME_API_TOKEN: /{print $2}' <<< "$RENDER" | tr -d '"')"
if [ "${#rt_secret}" -ge 32 ]; then echo "  OK: an empty secrets.runtimeApiToken renders a generated token"
else echo "  FAIL: no generated RUNTIME_API_TOKEN in the chart Secret"; fail=1; fi
refuse_rt="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set secrets.runtimeApiToken=short 2>&1 || true)"
if grep -q 'secrets.runtimeApiToken must be 32+ bytes' <<< "$refuse_rt"; then echo "  OK: a short secrets.runtimeApiToken is refused"
else echo "  FAIL: a short secrets.runtimeApiToken rendered"; fail=1; fi
# The chart's redis requires a password; each of its five clients expands it into its URL.
redis_dep="$(awk '/deployment-redis.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER")"
if grep -q -- '- "--requirepass"' <<< "$redis_dep" && grep -A1 -- '- "--requirepass"' <<< "$redis_dep" | grep -q '\$(REDIS_PASSWORD)'; then
  echo "  OK: redis requires a password"
else echo "  FAIL: redis does not require a password"; fail=1; fi
n_urls="$(grep -cE 'value: "redis://:\$\(REDIS_PASSWORD\)@vexa-vexa-redis' <<< "$RENDER" || true)"
if [ "$n_urls" = 5 ]; then echo "  OK: the five redis clients carry the password in their URL"
else echo "  FAIL: $n_urls redis URL(s) carry the password, want 5"; fail=1; fi
if grep -qE 'value: "redis://vexa-vexa-redis' <<< "$RENDER"; then
  echo "  FAIL: a redis URL without the password is rendered"; fail=1
else echo "  OK: no password-less redis URL"; fi
for f in deployment-admin-api deployment-agent-api deployment-gateway deployment-meeting-api deployment-runtime; do
  block="$(awk "/$f.yaml/{f=1} f{print} f&&/^---/{exit}" <<< "$RENDER")"
  pw_line="$(grep -n 'name: REDIS_PASSWORD$' <<< "$block" | head -1 | cut -d: -f1)"
  url_line="$(grep -nE 'name: (VEXA_)?REDIS_URL$' <<< "$block" | head -1 | cut -d: -f1)"
  if [ -n "$pw_line" ] && [ -n "$url_line" ] && [ "$pw_line" -lt "$url_line" ]; then :
  else echo "  FAIL: $f does not define REDIS_PASSWORD before its redis URL"; fail=1; fi
done
echo "  OK: every redis client defines REDIS_PASSWORD before the URL that expands it"
refuse_redis="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set secrets.redisPassword=short 2>&1 || true)"
if grep -q 'secrets.redisPassword must be 32+' <<< "$refuse_redis"; then echo "  OK: a short secrets.redisPassword is refused"
else echo "  FAIL: a short secrets.redisPassword rendered"; fail=1; fi
if grep -A1 'name: INTERNAL_API_SECRET$' <<< "$(awk '/deployment-runtime.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER")" | grep -q secretKeyRef; then
  echo "  FAIL: the runtime still carries INTERNAL_API_SECRET"; fail=1
else echo "  OK: the runtime carries no internal-tier secret"; fi

refuse() {  # refuse <label> <expected-message-regex> <helm args...>
  local label="$1" want="$2" out; shift 2
  if out="$(helm template vexa "$CHART" -n vexa "$@" 2>&1)"; then
    echo "  FAIL: $label — rendered, must refuse"; fail=1
  elif grep -qE "$want" <<< "$out"; then echo "  OK: $label → refused with the actionable message"
  else echo "  FAIL: $label — refused without the expected message: $(grep -m1 Error <<< "$out")"; fail=1; fi
}
refuse "no storage.s3 (chart defaults)" 'storage\.s3 is incomplete, missing: storage\.s3\.endpoint, storage\.s3\.bucket, storage\.s3\.existingSecret' \
  --set secrets.internalApiSecret=x
refuse "stale minio.enabled=true" 'minio\.enabled=true is no longer supported.*data-vexa-vexa-minio-0' \
  -f "$CHART/values-test.yaml" --set minio.enabled=true
refuse "endpoint without a scheme" 'storage\.s3\.endpoint must be a full URL' \
  -f "$CHART/values-test.yaml" --set storage.s3.endpoint=minio:9000
refuse "region with whitespace" 'storage\.s3\.region must be a region name such as us-east-1' \
  -f "$CHART/values-test.yaml" --set 'storage.s3.region=us east'
refuse "region with an INI injection" 'storage\.s3\.region must be a region name such as us-east-1' \
  -f "$CHART/values-test.yaml" --set $'storage.s3.region=us-east-1\nca_bundle=/tmp/other-ca'
refuse "no existingSecret" 'missing: storage\.s3\.existingSecret' \
  -f "$CHART/values-test.yaml" --set storage.s3.existingSecret=
refuse "CA bundle named twice" 'set configMapName or secretName, not both' \
  -f "$CHART/values-test.yaml" --set storage.s3.caBundle.configMapName=a --set storage.s3.caBundle.secretName=b
refuse "extraEnv still setting S3_ENDPOINT" 'meetingApi\.extraEnv sets S3_ENDPOINT' \
  -f "$CHART/values-test.yaml" --set-json 'meetingApi.extraEnv=[{"name":"S3_ENDPOINT","value":"http://old"}]'
for k in AWS_DEFAULT_REGION AWS_CA_BUNDLE AWS_PROFILE AWS_SHARED_CREDENTIALS_FILE; do
  refuse "extraEnv setting $k" \
    "meetingApi\\.extraEnv sets $k, which the chart now sets from storage\\.s3\\. Put the value in storage\\.s3 and remove it from meetingApi\\.extraEnv\\." \
    -f "$CHART/values-test.yaml" --set "meetingApi.extraEnv[0].name=$k" --set 'meetingApi.extraEnv[0].value=override'
done

# NEXTAUTH_SECRET has no published default: empty generates one, a published or short value refuses.
nextauth() {  # nextauth <helm args...> → the NEXTAUTH_SECRET the chart's Secret renders
  helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "$@" --show-only templates/secret.yaml \
    | sed -nE 's/^  NEXTAUTH_SECRET: "(.*)"$/\1/p' | head -1
}
GEN="$(nextauth)"
if [ "${#GEN}" -ge 32 ] && [ "$GEN" != "dev-nextauth-secret" ]; then
  echo "  OK: an empty secrets.nextauthSecret renders a generated ${#GEN}-character secret"
else echo "  FAIL: empty secrets.nextauthSecret rendered '$GEN'"; fail=1; fi
[ "$(nextauth)" != "$GEN" ] && echo "  OK: each render without a release Secret mints its own value" \
  || { echo "  FAIL: two renders minted the same NEXTAUTH_SECRET"; fail=1; }
GIVEN=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
[ "$(nextauth --set secrets.nextauthSecret=$GIVEN)" = "$GIVEN" ] && echo "  OK: a given secrets.nextauthSecret is used as is" \
  || { echo "  FAIL: a given secrets.nextauthSecret did not render"; fail=1; }
for weak in dev-nextauth-secret DEV-NEXTAUTH-SECRET vexa-lite-nextauth-secret short-secret; do
  refuse "secrets.nextauthSecret=$weak" 'secrets\.nextauthSecret must be 32\+ bytes and not a value published' \
    -f "$CHART/values-test.yaml" --set "secrets.nextauthSecret=$weak"
done
exact 0 'dev-nextauth-secret' "no published NEXTAUTH_SECRET in the default render"

# VEXA_DISPATCH_SIGNING_KEY has no published default either (agent-api refuses `dev-dispatch-signing-key`
# at boot): empty generates one, a published or short value refuses. Keeping it across upgrades is the
# lookup every generated key here uses; a Secret still holding the published value gets a new one.
dispatch_key() {  # dispatch_key <helm args...> → the VEXA_DISPATCH_SIGNING_KEY the chart's Secret renders
  helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "$@" --show-only templates/secret.yaml \
    | sed -nE 's/^  VEXA_DISPATCH_SIGNING_KEY: "(.*)"$/\1/p' | head -1
}
DGEN="$(dispatch_key)"
if [ "${#DGEN}" -ge 32 ] && [ "$DGEN" != "dev-dispatch-signing-key" ]; then
  echo "  OK: an empty secrets.dispatchSigningKey renders a generated ${#DGEN}-character key"
else echo "  FAIL: empty secrets.dispatchSigningKey rendered '${DGEN:0:8}…'"; fail=1; fi
[ "$(dispatch_key --set secrets.dispatchSigningKey=$GIVEN)" = "$GIVEN" ] && echo "  OK: a given secrets.dispatchSigningKey is used as is" \
  || { echo "  FAIL: a given secrets.dispatchSigningKey did not render"; fail=1; }
for weak in dev-dispatch-signing-key DEV-DISPATCH-SIGNING-KEY CHANGE-ME short-key; do
  refuse "secrets.dispatchSigningKey=$weak" 'secrets\.dispatchSigningKey must be 32\+ bytes and not a value published' \
    -f "$CHART/values-test.yaml" --set "secrets.dispatchSigningKey=$weak"
done
exact 0 'dev-dispatch-signing-key' "no published VEXA_DISPATCH_SIGNING_KEY in the default render"

# ADMIN_API_TOKEN has no published default either: it was `CHANGE_ME`, and admin-api, meeting-api and
# flows now refuse that and every other value this repository published for the admin key. Empty
# generates one (and an upgrade keeps the release Secret's, by lookup), a published value refuses.
admin_token() {  # admin_token <helm args...> → the ADMIN_API_TOKEN the chart's Secret renders
  helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "$@" --show-only templates/secret.yaml \
    | sed -nE 's/^  ADMIN_API_TOKEN: "(.*)"$/\1/p' | head -1
}
AGEN="$(admin_token)"
if [ "${#AGEN}" -ge 32 ] && [ "$AGEN" != "CHANGE_ME" ]; then
  echo "  OK: an empty secrets.adminApiToken renders a generated ${#AGEN}-character token"
else echo "  FAIL: empty secrets.adminApiToken rendered '${AGEN:0:8}…'"; fail=1; fi
[ "$(admin_token)" != "$AGEN" ] && echo "  OK: each render without a release Secret mints its own admin token" \
  || { echo "  FAIL: two renders minted the same ADMIN_API_TOKEN"; fail=1; }
[ "$(admin_token --set secrets.adminApiToken=$GIVEN)" = "$GIVEN" ] && echo "  OK: a given secrets.adminApiToken is used as is" \
  || { echo "  FAIL: a given secrets.adminApiToken did not render"; fail=1; }
for weak in CHANGE_ME changeme dev-admin-token test-admin-token ci-admin-token your-secret token; do
  refuse "secrets.adminApiToken=$weak" 'secrets\.adminApiToken is a value published in the Vexa repository' \
    -f "$CHART/values-test.yaml" --set "secrets.adminApiToken=$weak"
done
grep -q 'adminApiToken: ""' "$CHART/values.yaml" && echo "  OK: values.yaml ships no admin token" \
  || { echo "  FAIL: values.yaml ships a default secrets.adminApiToken"; fail=1; }
exact 0 '^  ADMIN_API_TOKEN: "(CHANGE_ME|test-admin-token)"$' "no published ADMIN_API_TOKEN in the default render"

# The admin list is admin-api's (VEXA_ADMIN_EMAILS); an install that still sets it in the terminal's
# extraEnv, as this chart once said to, keeps its admins on upgrade.
admin_emails() {  # admin_emails <helm args...> → the value admin-api's VEXA_ADMIN_EMAILS renders to
  # awk reads the whole render: a reader that stops early SIGPIPEs helm, and under pipefail that
  # failed this script (exit 141) whenever the render outran the pipe buffer.
  helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "$@" \
    | awk '/name: vexa-vexa-admin-api$/{d=1} d && !done && /name: VEXA_ADMIN_EMAILS/{getline; print; done=1}' \
    | sed -E 's/.*value: "?([^"]*)"?/\1/'
}
check_admins() {  # check_admins <label> <want> <helm args...>
  local label="$1" want="$2" got; shift 2
  got="$(admin_emails "$@")"
  if [ "$got" = "$want" ]; then echo "  OK: $label"; else echo "  FAIL: $label — want '$want' got '$got'"; fail=1; fi
}
check_admins "adminApi.adminEmails reaches admin-api" "a@example.com" --set adminApi.adminEmails=a@example.com
check_admins "terminal.extraEnv VEXA_ADMIN_EMAILS is carried over to admin-api" "old@example.com" \
  --set 'terminal.extraEnv[0].name=VEXA_ADMIN_EMAILS' --set 'terminal.extraEnv[0].value=old@example.com'
check_admins "unset is empty" ""

# ── Connections: the credential broker (credential-broker.v1, ADR-0040) ──────────────────────────
# The broker renders with agent-api, admits only agent-api and terminal Pods, and each consumer
# mounts only its own role key: agent-api never the human key, the terminal never the agent key.
echo "=== credential broker ==="
need 1 'name: vexa-vexa-credential-broker$'        "broker Deployment/Service name"
need 1 '^kind: NetworkPolicy'                      "broker NetworkPolicy"
need 1 'name: vexa-vexa-credential-broker-state'   "broker state PVC"
need 1 'helm.sh/resource-policy: keep'             "broker keys Secret kept across uninstall"
need 2 'name: VEXA_CONNECTIONS_BROKER_URL'         "broker URL on agent-api AND terminal"
need 1 'name: VEXA_CONNECTIONS_AGENT_KEY_FILE'     "agent role key path"
need 1 'name: VEXA_CONNECTIONS_HUMAN_KEY_FILE'     "human role key path"
# 4 component labels: the policy's own two (metadata + podSelector) and the two admitted sources.
NP="$(awk '/Source: vexa\/templates\/networkpolicy-credential-broker.yaml/,/^---/' <<< "$RENDER")"
if grep -q 'component: agent-api' <<< "$NP" && grep -q 'component: terminal' <<< "$NP" \
   && [ "$(grep -c 'component: ' <<< "$NP")" -eq 4 ]; then echo "  OK: NetworkPolicy admits exactly agent-api + terminal"
else echo "  FAIL: NetworkPolicy ingress is not exactly agent-api + terminal"; fail=1; fi
AGENT="$(awk '/Source: vexa\/templates\/deployment-agent-api.yaml/,/^---/' <<< "$RENDER")"
TERM="$(awk '/Source: vexa\/templates\/deployment-terminal.yaml/,/^---/' <<< "$RENDER")"
if grep -q 'key: agent.key' <<< "$AGENT" && ! grep -q 'key: human.key' <<< "$AGENT" && ! grep -q 'key: git.key' <<< "$AGENT"; then
  echo "  OK: agent-api mounts the agent key only (gitStore off)"
else echo "  FAIL: agent-api key mounts wrong"; fail=1; fi
if grep -q 'key: human.key' <<< "$TERM" && ! grep -qE 'key: (agent|git|store).key' <<< "$TERM"; then
  echo "  OK: terminal mounts the human key only"
else echo "  FAIL: terminal key mounts wrong"; fail=1; fi
GIT="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set credentialBroker.gitStore=true)"
if [ "$(grep -c 'name: VEXA_GIT_STORE_BROKER_URL' <<< "$GIT")" -eq 1 ] && grep -q 'key: git.key, path: git/key' <<< "$GIT"; then
  echo "  OK: gitStore=true wires the git role into agent-api"
else echo "  FAIL: gitStore=true did not wire the git store"; fail=1; fi
NOAGENT="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set agentApi.enabled=false)"
if ! grep -q 'credential-broker' <<< "$NOAGENT"; then echo "  OK: no agent-api → no broker (no-agents profile)"
else echo "  FAIL: broker rendered without agent-api"; fail=1; fi
OAUTH="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set terminal.publicUrl=https://app.example.test/ \
  --set credentialBroker.google.clientId=x.apps.googleusercontent.com --set credentialBroker.google.clientSecret=s)"
if grep -q 'value: "https://app.example.test/api/auth/callback/google"' <<< "$OAUTH" \
   && grep -q 'key: google-client-secret' <<< "$OAUTH"; then echo "  OK: Google consent derives the callback from terminal.publicUrl"
else echo "  FAIL: Google consent wiring"; fail=1; fi
refuse "openbao backend without an address" 'credentialBroker\.openbao\.address is required' \
  -f "$CHART/values-test.yaml" --set credentialBroker.store.backend=openbao
EXT="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set credentialBroker.existingSecret=my-keys)"
if ! grep -q 'name: vexa-vexa-credential-broker-keys' <<< "$EXT" && [ "$(grep -c 'secretName: my-keys' <<< "$EXT")" -eq 3 ]; then
  echo "  OK: existingSecret replaces the generated keys on all three consumers"
else echo "  FAIL: existingSecret wiring"; fail=1; fi

# agent-api's VEXA_WORKSPACES_DIR, its store mountPath and the runtime's VEXA_WORKSPACE_MOUNT_TARGET
# are one path: the runtime refuses every mount outside its target, so a difference refuses every dispatch.
aa_block="$(awk '/deployment-agent-api.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER")"
rt_block="$(awk '/deployment-runtime.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER")"
ws_dir="$(grep -A1 'name: VEXA_WORKSPACES_DIR$' <<< "$aa_block" | sed -n 's/.*value: "\(.*\)"/\1/p')"
ws_target="$(grep -A1 'name: VEXA_WORKSPACE_MOUNT_TARGET$' <<< "$rt_block" | sed -n 's/.*value: "\(.*\)"/\1/p')"
ws_mount="$(grep -B1 'mountPath: ' <<< "$aa_block" | grep -A1 'name: workspaces$' | sed -n 's/.*mountPath: //p')"
if [ -n "$ws_dir" ] && [ "$ws_dir" = "$ws_target" ] && [ "$ws_dir" = "$ws_mount" ]; then
  echo "  OK: agent-api's workspace dir, its store mount and the runtime's mount target agree ($ws_dir)"
else echo "  FAIL: workspace dir '$ws_dir', store mount '$ws_mount', runtime target '$ws_target'"; fail=1; fi
# The runtime serves no out-of-store mount source unless agent-api's _global tier is configured.
if grep -A1 'name: RUNTIME_EXTRA_MOUNT_SOURCES$' <<< "$rt_block" | grep -q 'value: ""'; then :
else echo "  FAIL: the runtime serves an out-of-store mount source nobody configured"; fail=1; fi
glob_render="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set agentApi.globalSystemWorkspacePath=/srv/global)"
glob_rt="$(awk '/deployment-runtime.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$glob_render")"
if grep -A1 'name: RUNTIME_EXTRA_MOUNT_SOURCES$' <<< "$glob_rt" | grep -q 'value: "/srv/global"'; then
  echo "  OK: the runtime serves agent-api's _global tier as its one out-of-store mount source"
else echo "  FAIL: agentApi.globalSystemWorkspacePath does not reach RUNTIME_EXTRA_MOUNT_SOURCES"; fail=1; fi

# The bundled database's password: no published default; an explicit value must be 32+ bytes and not
# published; empty generates one (an existing Secret's value is kept by lookup, live-tested on a cluster).
pg_pw="$(awk '/^  name: postgres-credentials$/{f=1} f&&/^  POSTGRES_PASSWORD: /{print $2; exit}' <<< "$RENDER" | tr -d '"')"
if [ "${#pg_pw}" -ge 32 ] && [ "$pg_pw" != "postgres" ]; then echo "  OK: the bundled database gets a generated password"
else echo "  FAIL: the bundled database password is '${pg_pw:0:8}…' (${#pg_pw} chars)"; fail=1; fi
for bad in postgres POSTGRES changeme short-but-not-published; do
  out="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set database.password="$bad" 2>&1 || true)"
  if grep -q 'database.password must be 32+ bytes and not a value published' <<< "$out"; then :
  else echo "  FAIL: database.password=$bad rendered"; fail=1; fi
done
echo "  OK: an explicit published or short database.password is refused"
good="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
# Render first, then search: `helm … | grep -q` stops reading at the first match, SIGPIPEs helm, and
# under pipefail reports the match as a failure (and a refusal as a pass).
good_render="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set database.password="$good")"
if grep -q "POSTGRES_PASSWORD: \"$good\"" <<< "$good_render"; then
  echo "  OK: an explicit 32+ byte database.password is used as given"
else echo "  FAIL: an explicit database.password was not rendered"; fail=1; fi
# The rotation hook renders only on an upgrade whose live Secret holds a published value (lookup);
# an offline render never sees one, so it renders none. Its run is proven on a live cluster.
upgrade_render="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --is-upgrade)"
if grep -q 'postgres-password' <<< "$upgrade_render"; then
  echo "  FAIL: the postgres-password hook rendered without a published live Secret"; fail=1
else echo "  OK: no postgres-password hook without a published live Secret"; fi
# Its run is live-only (lookup), so its source is held here: the hook's rights go when it fails as
# well as when it succeeds, and no password is ever on a kubectl command line.
HOOK_SRC="$CHART/templates/job-postgres-password.yaml"
if [ "$(grep -c 'hook-delete-policy": before-hook-creation,hook-succeeded,hook-failed' "$HOOK_SRC")" -eq 3 ] \
   && ! grep -qE 'patch secret[^|]*-p "' "$HOOK_SRC" && grep -q -- '--patch-file /dev/stdin' "$HOOK_SRC"; then
  echo "  OK: the rotation hook's rights are removed on failure too; its patches go on stdin"
else echo "  FAIL: the rotation hook keeps its rights after a failure or puts a value on a command line"; fail=1; fi

# N-10: a policy that names the flows tier names this release's flows Pods (name + instance labels,
# which the flows Pods now carry), never any Pod in the namespace with a flows component label.
NP_FLOWS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
  | awk 'BEGIN{RS="\n---\n"} /kind: NetworkPolicy/')"
loose="$(awk '{ buf[NR%9]=$0 }
  /values: \[flows-worker|values: \["flows-worker"|component: flows-api$/ {
    ok=0; for (i=1;i<9;i++) if (buf[(NR-i)%9] ~ /app.kubernetes.io\/instance: vexa/) ok=1
    if (!ok) n++ }
  END { print n+0 }' <<< "$NP_FLOWS")"
pod_labels="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --show-only templates/flows.yaml \
  | grep -c 'app.kubernetes.io/instance: vexa' || true)"
if [ "$loose" -eq 0 ] && [ "$(grep -c 'flows' <<< "$NP_FLOWS")" -gt 0 ] && [ "$pod_labels" -ge 4 ]; then
  echo "  OK: every policy peer for the flows tier carries the release's labels, and so do its Pods and Service"
else echo "  FAIL: $loose flows policy peer(s) match on the component label alone (flows Pod/Service labels: $pod_labels)"; fail=1; fi

# Redis holds the delegation live and revocation records (R6-12): it never evicts. The render passes
# noeviction and a maxmemory below the container's memory limit, and refuses any eviction policy.
redis_doc="$(awk 'BEGIN{RS="\n---\n"} /kind: Deployment/ && /component: redis/' <<< "$RENDER")"
policy="$(grep -A1 -- '"--maxmemory-policy"' <<< "$redis_doc" | tail -1 | tr -d ' "-')"
maxmem="$(grep -A1 -- '"--maxmemory"' <<< "$redis_doc" | tail -1 | tr -d ' "-')"
if [ "$policy" = "noeviction" ] && [ "$maxmem" = "768mb" ] && grep -q 'memory: 1Gi' <<< "$redis_doc"; then
  echo "  OK: Redis runs noeviction with maxmemory (768mb) under its 1Gi limit"
else echo "  FAIL: Redis policy '$policy', maxmemory '$maxmem' — it must never evict security state"; fail=1; fi
for bad in allkeys-lru volatile-lru allkeys-random volatile-ttl; do
  if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set redis.maxmemoryPolicy="$bad" >/dev/null 2>&1; then
    echo "  FAIL: redis.maxmemoryPolicy=$bad rendered"; fail=1
  fi
done
echo "  OK: an eviction policy for Redis is refused at render"

# The runtime creates each spawned Pod's credential Secret and may do nothing else with Secrets.
role="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/rbac-runtime.yaml \
  | sed -n '/^kind: Role$/,/^---/p' | grep -v '^ *#')"
if grep -A1 'resources: \["secrets"\]' <<< "$role" | grep -q 'verbs: \["create"\]$' \
   && [ "$(grep -ci 'secret' <<< "$role")" -eq 1 ]; then
  echo "  OK: the runtime may create Secrets (a spawned Pod's credential env) and nothing more"
else echo "  FAIL: the runtime's Role over Secrets is not create-only"; fail=1; fi

# N-7: the namespace default-deny, both directions, with explicit allows for the chart's Pods.
np_doc() { awk -v n="name: $1" '$0 ~ "^  "n"$"{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER"; }
dd="$(np_doc vexa-vexa-default-deny)"
if grep -q '^  podSelector: {}$' <<< "$dd" && grep -q 'policyTypes: \[Ingress, Egress\]' <<< "$dd"; then
  echo "  OK: a namespace-wide default-deny, ingress and egress"
else echo "  FAIL: no namespace-wide default-deny"; fail=1; fi
for c in gateway terminal admin-api mcp postgres redis agent-api meeting-api runtime; do
  pol="$(np_doc "vexa-vexa-$c-ingress")"
  if grep -q "app.kubernetes.io/component: $c$" <<< "$pol"; then :
  else echo "  FAIL: no ingress allow for $c under the default-deny"; fail=1; fi
done
echo "  OK: every serving component has an explicit ingress allow"
pg="$(np_doc vexa-vexa-postgres-ingress)"
if grep -q 'values: \[admin-api, meeting-api, pgbouncer, migrations\]' <<< "$pg" \
   && ! grep -qE 'runtime.managed|component: (gateway|runtime|agent-api|mcp|terminal)$' <<< "$pg"; then
  echo "  OK: only the database's credential holders reach postgres"
else echo "  FAIL: postgres admits more than its credential holders"; fail=1; fi
cpe="$(np_doc vexa-vexa-control-plane-egress)"
if grep -q 'values:' <<< "$cpe" && grep -A3 'operator: NotIn' <<< "$cpe" | grep -q -- '- redis' \
   && grep -A1 'except:' <<< "$cpe" | grep -q -- '- 169.254.0.0/16'; then
  echo "  OK: control-plane egress leaves out postgres and redis and the metadata range"
else echo "  FAIL: control-plane egress"; fail=1; fi
if grep -q -- '- 10.0.0.0/8' <<< "$cpe"; then echo "  FAIL: control-plane egress refuses private ranges by default"; fail=1; fi
off="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set networkPolicy.defaultDeny.enabled=false)"
if grep -q 'name: vexa-vexa-default-deny' <<< "$off"; then echo "  FAIL: defaultDeny.enabled=false still renders the default-deny"; fail=1
else echo "  OK: networkPolicy.defaultDeny.enabled=false renders no default-deny"; fi
fl_render="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true)"
fl="$(awk '/^  name: vexa-vexa-flows-api-ingress$/{f=1} f{print} f&&/^---/{exit}' <<< "$fl_render")"
if grep -q 'vexa.role: worker' <<< "$fl"; then echo "  OK: flows-api admits the chart's Pods and agent workers"
else echo "  FAIL: flows-api ingress under the default-deny"; fail=1; fi

# The broker answers readiness on /ready (503 while its store does not), liveness on /health.
broker="$(awk '/deployment-credential-broker.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER")"
if grep -A2 'readinessProbe:' <<< "$broker" | grep -q 'path: /ready'; then echo "  OK: the broker's readiness is /ready"
else echo "  FAIL: the broker's readinessProbe is not /ready"; fail=1; fi
# Every generated secret has a way to stay put under a template-only render (GitOps): an existing
# Secret the chart reads instead of generating.
dbx="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set database.existingSecret=my-db)"
if grep -q '^  name: postgres-credentials$' <<< "$dbx"; then echo "  FAIL: database.existingSecret still renders the credentials Secret"; fail=1
elif [ "$(grep -cE 'name: "?my-db"?$' <<< "$dbx")" -ge 3 ]; then echo "  OK: database.existingSecret is the bundled database's and every consumer's credentials"
else echo "  FAIL: database.existingSecret is not wired to the database and its consumers"; fail=1; fi
idx="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set identity.existingSecret=my-id --set-string identity.publicKey=PUBLIC-KEY-PEM)"
if grep -q '^  name: vexa-vexa-identity-signing-key$' <<< "$idx"; then echo "  FAIL: identity.existingSecret still renders a generated signing key"; fail=1
elif grep -q 'secretName: my-id' <<< "$idx" && grep -q 'PUBLIC-KEY-PEM' <<< "$idx"; then
  echo "  OK: identity.existingSecret is the gateway's signing key, its public key given beside it"
else echo "  FAIL: identity.existingSecret wiring"; fail=1; fi
idmiss="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set identity.existingSecret=my-id 2>&1 || true)"
if grep -q 'identity.publicKey is required with identity.existingSecret' <<< "$idmiss"; then echo "  OK: identity.existingSecret without its public key is refused"
else echo "  FAIL: identity.existingSecret rendered without a public key"; fail=1; fi

# flows' operator key (VEXA_FLOWS_API_KEY) from a Secret you manage, so a template-only render can
# turn flows on without the key in values: flows.existingSecret, or secrets.existingSecretName when
# flows.apiKey is empty. Then the chart's flows Secret omits the key and all four consumers (three
# flows containers, the MCP edge) read it from that Secret by name. With flows.apiKey set, nothing
# changes; both apiKey and existingSecret is refused.
flows_key_refs() {  # flows_key_refs <render> → "<secret> <count>" lines for VEXA_FLOWS_API_KEY refs
  grep -A4 -E '^[[:space:]]+- name: VEXA_FLOWS_API_KEY$' <<< "$1" | grep 'key: VEXA_FLOWS_API_KEY' -B1 \
    | sed -nE 's/^[[:space:]]+name: "?([^"]*)"?$/\1/p' | sort | uniq -c | awk '{print $2" "$1}'
}
for src in "secrets.existingSecretName=shared-secrets shared-secrets" "flows.existingSecret=flows-key flows-key"; do
  set -- $src
  fk="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --set "$1")"
  if grep -qE '^  VEXA_FLOWS_API_KEY:' <<< "$fk"; then echo "  FAIL: $1 still renders the key into the flows Secret"; fail=1
  elif [ "$(flows_key_refs "$fk")" = "$2 4" ]; then echo "  OK: $1 — flows' three containers and the MCP edge read VEXA_FLOWS_API_KEY from $2"
  else echo "  FAIL: $1 — VEXA_FLOWS_API_KEY refs: $(flows_key_refs "$fk" | tr '\n' ';')"; fail=1; fi
done
fk="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
  --set secrets.existingSecretName=shared-secrets --set flows.apiKey=inline-test-key)"
if grep -qE '^  VEXA_FLOWS_API_KEY: "inline-test-key"$' <<< "$fk" && [ "$(flows_key_refs "$fk")" = "vexa-vexa-flows 1" ]; then
  echo "  OK: flows.apiKey in values keeps the key in the chart's flows Secret, as before"
else echo "  FAIL: flows.apiKey with secrets.existingSecretName"; fail=1; fi
fboth="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
  --set flows.existingSecret=flows-key --set flows.apiKey=inline-test-key 2>&1 || true)"
if grep -q 'flows.apiKey and flows.existingSecret are both set' <<< "$fboth"; then echo "  OK: flows.apiKey with flows.existingSecret is refused"
else echo "  FAIL: flows.apiKey with flows.existingSecret rendered"; fail=1; fi

# The terminal believes X-Forwarded-For only from the proxies it is told about. ClusterIP (only the
# ingress and pods reach it): the in-cluster ranges. Any other Service type: nothing by default.
tp() { grep -A1 'name: TERMINAL_TRUSTED_PROXIES' <<< "$1" | sed -n 's/.*value: //p'; }
if [ "$(tp "$RENDER")" = '"10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,fc00::/7"' ]; then
  echo "  OK: terminal trusts the in-cluster ranges behind a ClusterIP Service"
else echo "  FAIL: terminal TERMINAL_TRUSTED_PROXIES behind ClusterIP: $(tp "$RENDER")"; fail=1; fi
tlb="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set terminal.service.type=LoadBalancer)"
if [ "$(tp "$tlb")" = '""' ]; then echo "  OK: a LoadBalancer terminal trusts no proxy unless one is named"
else echo "  FAIL: LoadBalancer terminal TERMINAL_TRUSTED_PROXIES: $(tp "$tlb")"; fail=1; fi
tnamed="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set terminal.service.type=LoadBalancer --set terminal.trustedProxies=203.0.113.10)"
if [ "$(tp "$tnamed")" = '"203.0.113.10"' ]; then echo "  OK: terminal.trustedProxies is passed through as named"
else echo "  FAIL: named TERMINAL_TRUSTED_PROXIES: $(tp "$tnamed")"; fail=1; fi

# The flows tier is matched by release, not by component alone: its Pods carry the chart's
# selector labels, and every NetworkPolicy podSelector, the flows-api Service selector and the flows
# Pod templates that name a flows component also name this release, so another release's flows Pods
# in the namespace are not peers. (The Deployments' own selectors stay as they are: immutable.)
flows_np="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true)"
# Prints each such block that names a flows component without this release's instance label.
loose="$(awk '
  function ind(s) { match(s, /^ */); return RLENGTH }
  function check(i,   d, j, blk, f, k) {
    d = ind(line[i]); blk = line[i]; f = (line[i] ~ /flows-(worker|api|mailbox)/); k = 0
    for (j = i + 1; j <= n && ind(line[j]) > d; j++) {
      blk = blk "\n" line[j]
      if (line[j] ~ /flows-(worker|api|mailbox)/) f = 1
      if (line[j] ~ /app.kubernetes.io\/instance: vexa/) k = 1
    }
    if (f && !k) print kind ": " blk "\n=="
  }
  function flush(   i) {
    for (i = 1; i <= n; i++) {
      if (kind == "NetworkPolicy" && line[i] ~ /^ *(- )?podSelector:/) check(i)
      if (kind == "Service" && line[i] ~ /^  selector:/) check(i)
      if (kind == "Deployment" && line[i] ~ /^      labels:/) check(i)
    }
    n = 0; kind = ""
  }
  /^---/ { flush(); next }
  /^kind: / { kind = $2 }
  { line[++n] = $0 }
  END { flush() }' <<< "$flows_np")"
if [ -z "$loose" ] && grep -q 'app.kubernetes.io/component: flows-api' <<< "$flows_np"; then
  echo "  OK: every policy, Service and Pod template naming a flows component also names this release"
else echo "  FAIL: flows matched by component alone:"; echo "$loose" | head -40; fail=1; fi

# ── Generic OIDC sign-in (ADFS / Keycloak) — terminal.oidc + terminal.signinMethods ────────────────
# Off by default: no VEXA_OIDC_* and no CA volume. On: issuer + client id as values, the client secret
# ONLY through the operator's existing Secret, the CA bundle mounted from a ConfigMap or a Secret, and
# a half-filled block refuses to render.
TERM_DEFAULT="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-terminal.yaml)"
if grep -q 'VEXA_OIDC_' <<< "$TERM_DEFAULT" || grep -q 'oidc-ca' <<< "$TERM_DEFAULT" || grep -q 'VEXA_SIGNIN_METHODS' <<< "$TERM_DEFAULT"; then
  echo "  FAIL: OIDC env or CA volume rendered with terminal.oidc unset"; fail=1
else echo "  OK: no OIDC wiring by default"; fi
TERM_OIDC="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-terminal.yaml \
  --set terminal.oidc.issuer=https://adfs.example.test/adfs --set terminal.oidc.clientId=vexa-terminal \
  --set terminal.oidc.existingSecret=vexa-oidc --set terminal.oidc.scopes='openid email profile allatclaims' \
  --set terminal.oidc.caBundle.configMap=corp-ca --set terminal.signinMethods=oidc --set terminal.oidc.resource=vexa-terminal)"
for want in 'name: VEXA_OIDC_ISSUER' 'value: "https://adfs.example.test/adfs"' 'name: VEXA_OIDC_CLIENT_ID' \
            'value: "openid email profile allatclaims"' 'name: VEXA_OIDC_CA_FILE' 'value: /etc/vexa-oidc/ca/ca.pem' \
            'mountPath: /etc/vexa-oidc/ca' 'name: "corp-ca"' 'path: ca.pem' 'name: VEXA_SIGNIN_METHODS' 'value: "oidc"' 'name: VEXA_OIDC_RESOURCE'; do
  if grep -qF -- "$want" <<< "$TERM_OIDC"; then echo "  OK: oidc renders $want"; else echo "  FAIL: oidc render lacks $want"; fail=1; fi
done
if awk '/name: VEXA_OIDC_CLIENT_SECRET/{f=1;next} f&&/secretKeyRef:/{s=1} f&&s&&/name: "vexa-oidc"/{n=1} f&&s&&/key: "client-secret"/{k=1} f&&/- name:/{exit} END{exit !(n&&k)}' <<< "$TERM_OIDC"; then
  echo "  OK: the OIDC client secret comes from the existing Secret"
else echo "  FAIL: VEXA_OIDC_CLIENT_SECRET is not a secretKeyRef to terminal.oidc.existingSecret"; fail=1; fi
TERM_OIDC_SECRET_CA="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-terminal.yaml \
  --set terminal.oidc.issuer=https://kc.example.test/realms/corp --set terminal.oidc.clientId=vexa-terminal \
  --set terminal.oidc.existingSecret=vexa-oidc --set terminal.oidc.caBundle.secret=corp-ca-secret --set terminal.oidc.caBundle.key=chain.pem)"
if grep -qF 'secretName: "corp-ca-secret"' <<< "$TERM_OIDC_SECRET_CA" && grep -qF 'key: "chain.pem"' <<< "$TERM_OIDC_SECRET_CA"; then
  echo "  OK: the OIDC CA bundle can come from a Secret"
else echo "  FAIL: terminal.oidc.caBundle.secret did not mount"; fail=1; fi
for bad in "--set terminal.oidc.issuer=https://x.test --set terminal.oidc.clientId=c" \
           "--set terminal.oidc.issuer=https://x.test --set terminal.oidc.existingSecret=s" \
           "--set terminal.oidc.issuer=https://x.test --set terminal.oidc.clientId=c --set terminal.oidc.existingSecret=s --set terminal.oidc.caBundle.configMap=a --set terminal.oidc.caBundle.secret=b"; do
  # shellcheck disable=SC2086
  if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" $bad >/dev/null 2>&1; then
    echo "  FAIL: a half-filled terminal.oidc rendered: $bad"; fail=1
  else echo "  OK: refused to render: $bad"; fi
done

[ "$fail" -eq 0 ] && { echo "gate:helm PASS"; exit 0; } || { echo "gate:helm FAIL"; exit 1; }
