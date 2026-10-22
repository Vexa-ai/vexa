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
# A PIPELINE'S READER READS TO THE END. Under `set -o pipefail` a reader that exits at its first match
# (`grep -q`, `head -1`, `grep -m1`) can close the pipe while the writer (a `helm template` render,
# a here-string through `grep -A1`) is still writing; the writer dies of SIGPIPE (141) and the row
# fails, or under `!` passes, on a loaded host. `has` is `grep -q` that reads all of its input.
has() { grep "$@" >/dev/null; }
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
# The admin account (the only writer of _global) is designated on admin-api and nowhere else; the
# retired agent-api id list must never render again (one writer: identity).
need 1 'name: VEXA_ADMIN_ACCOUNT' "admin-api carries VEXA_ADMIN_ACCOUNT"
if grep -q 'VEXA_GLOBAL_ADMIN_SUBJECTS' <<< "$RENDER"; then
  echo "  FAIL: the retired VEXA_GLOBAL_ADMIN_SUBJECTS is rendered"; fail=1
else
  echo "  OK: VEXA_GLOBAL_ADMIN_SUBJECTS is not rendered"
fi
# model-auth wiring: worker creds ride the dispatch spec env FROM agent-api, so agent-api must
# carry the optional secret refs (values-test leaves auth unset — CI has no creds; render + boot
# must stay green, the env ref is optional:true).
need 1 'key: CLAUDE_CODE_OAUTH_TOKEN' "agent-api CLAUDE_CODE_OAUTH_TOKEN secret ref"
need 1 'key: ANTHROPIC_AUTH_TOKEN'    "ANTHROPIC_AUTH_TOKEN secret ref (agent-api only: no worker holds it, H5/W1)"
need 2 'name: MEETING_API_URL' "MEETING_API_URL set on gateway AND meeting-api"
# A terminating meeting-api must stay alive until EndpointSlice/kube-proxy stops routing its IP.
# Without this drain, deleting co-located replicas produces immediate connection refusals even
# while another replica remains Ready.
need 1 'terminationGracePeriodSeconds: 30' "meeting-api termination budget rendered"
need 1 '^[[:space:]]+- "sleep 5"$' "meeting-api preStop endpoint-drain delay rendered"
# F13: the schema's migrations path runs as a PreSync/pre-upgrade Job BEFORE any new admin-api or
# meeting-api pod starts, so both roll new-beside-old. It is admin-api's own convergence
# (`python -m admin_api.migrate`, additive) from the admin-api image — the meeting_api.database
# module the old Job called no longer exists.
MIG="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set migrations.enabled=true --show-only templates/job-migrations.yaml)"
if grep -q 'admin_api.migrate' <<< "$MIG" && grep -q 'vexa-admin-api' <<< "$MIG" && ! grep -q 'meeting_api.database' <<< "$MIG" \
   && grep -q '"helm.sh/hook": pre-upgrade' <<< "$MIG"; then
  echo "  OK: migrations Job runs admin_api.migrate from the admin-api image, pre-upgrade"
else
  echo "  FAIL: migrations Job does not run admin_api.migrate from the admin-api image before the upgrade"; fail=1
fi
# #677: agent-api MUST get VEXA_MEETING_API_URL or its live-SSE owner-lookup calls the compose-only
# http://meeting-api:8080 (unresolvable in-cluster) → fail-closed 403 for the meeting's own owner.
# Only agent-api carries the VEXA_-prefixed spelling, so assert exactly 1.
need 1 'name: VEXA_MEETING_API_URL' "agent-api meeting-api URL (owner-scope)"
# agent-api reaches identity: the workspace registry, and the principal a room's turn runs as. Without
# it a room's turn has no toolbelt and membership lives in files on the workspace volume.
agent_api_block="$(awk '/deployment-agent-api.yaml/{f=1} f&&/^---/{exit} f{print}' <<< "$RENDER")"
if grep -q 'value: "http://vexa-vexa-admin-api:8001"' <<< "$agent_api_block" \
   && grep -A1 'name: VEXA_ROOM_WS_PRINCIPAL' <<< "$agent_api_block" | has 'value: "true"'; then
  echo "  OK: agent-api reaches admin-api and runs a room's turn as its workspace principal"
else
  echo "  FAIL: agent-api lacks VEXA_ADMIN_API_URL or VEXA_ROOM_WS_PRINCIPAL=true"; fail=1
fi
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
            'dest="/host-seccomp/vexa/seccomp-userns.json"' 'image: "vexaai/vexa-runtime:'; do
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
  if grep -A6 'topologySpreadConstraints:' <<< "$RENDER_TSC" | has -E "app.kubernetes.io/component: ${comp}\$"; then
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
# the only two single-PVC opt-outs, so exactly 7 RollingUpdate blocks remain (gateway, admin-api,
# meeting-api, runtime, terminal, mcp, model-proxy).
roll_count="$(grep -cE '^    type: RollingUpdate' <<< "$RENDER" || true)"
if [ "$roll_count" -eq 7 ]; then
  echo "  OK: 7 non-PVC Deployments keep RollingUpdate (agent-api + redis excepted)"
else
  echo "  FAIL: expected 7 RollingUpdate Deployments, got $roll_count"; fail=1
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

# #1783 — calls: the LiveKit server is OPT-IN (absent by default, so the counts above never grow by
# it); enabled, it renders its Deployment + Service, meeting-api gets the LIVEKIT_* env, and the
# release Secret carries a generated LIVEKIT_API_SECRET.
if grep -q 'app.kubernetes.io/component: livekit' <<< "$RENDER"; then
  echo "  FAIL: livekit rendered in the DEFAULT (disabled) state"; fail=1
else
  echo "  OK: livekit absent by default (calls are opt-in)"
fi
RENDER_LK="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set livekit.enabled=true --set livekit.url=wss://calls.example.test)"
lk_count="$(grep -c 'app.kubernetes.io/component: livekit' <<< "$RENDER_LK" || true)"
if [ "$lk_count" -ge 3 ] && grep -q 'value: "ws://vexa-vexa-livekit:7880"' <<< "$RENDER_LK" \
   && grep -q 'LIVEKIT_API_SECRET: "' <<< "$RENDER_LK"; then
  echo "  OK: livekit.enabled=true renders the server, its Service, the services' env and the secret"
else
  echo "  FAIL: livekit.enabled=true did not render server + Service + env + secret ($lk_count labels)"; fail=1
fi
# The LiveKit pod runs unprivileged (non-root, no privilege escalation, every capability dropped),
# and its signalling port binds the pod's own address, not every interface of the node.
LK_DEP="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set livekit.enabled=true --set livekit.url=wss://calls.example.test \
  --show-only templates/deployment-livekit.yaml)"
if grep -q 'runAsNonRoot: true' <<< "$LK_DEP" && grep -q 'allowPrivilegeEscalation: false' <<< "$LK_DEP" \
   && grep -qE '^\s+- ALL$' <<< "$LK_DEP" && grep -q 'fieldPath: status.podIP' <<< "$LK_DEP" \
   && grep -q 'bind_addresses: \["\$(POD_IP)"\]' <<< "$LK_DEP"; then
  echo "  OK: livekit runs non-root with every capability dropped, signalling on the pod IP"
else
  echo "  FAIL: livekit Deployment lacks the non-root/drop-ALL context or the pod-IP signalling bind"; fail=1
fi
LK_BIND="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set livekit.enabled=true --set livekit.url=wss://calls.example.test \
  --set 'livekit.bindAddresses={127.0.0.1}' --show-only templates/deployment-livekit.yaml)"
if grep -q 'bind_addresses: \["127.0.0.1"\]' <<< "$LK_BIND"; then
  echo "  OK: livekit.bindAddresses overrides the signalling bind"
else
  echo "  FAIL: livekit.bindAddresses did not reach the server config"; fail=1
fi
# TURN over TLS is off by default; on, it renders the TLS port, the certificate paths and the Secret
# mount, and it refuses to render without a domain and a Secret, or on a privileged port.
LK_TLS_ARGS=(--set livekit.enabled=true --set livekit.url=wss://calls.example.test --set livekit.turn.enabled=true
  --set livekit.turn.domain=turn.example.test --set livekit.turn.tlsPort=5349 --set livekit.turn.tlsSecretName=turn-tls)
LK_TLS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${LK_TLS_ARGS[@]}" \
  --show-only templates/deployment-livekit.yaml)"
if ! grep -q 'tls_port' <<< "$LK_DEP" && grep -q 'tls_port: 5349' <<< "$LK_TLS" \
   && grep -q 'cert_file: /etc/livekit/turn/tls.crt' <<< "$LK_TLS" && grep -q 'secretName: turn-tls' <<< "$LK_TLS"; then
  echo "  OK: livekit TURN over TLS is off by default and renders port, certificate and Secret when on"
else
  echo "  FAIL: livekit TURN over TLS did not render as expected"; fail=1
fi
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${LK_TLS_ARGS[@]}" \
     --set livekit.turn.tlsSecretName= >/dev/null 2>&1 \
   || helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${LK_TLS_ARGS[@]}" \
     --set livekit.turn.tlsPort=443 >/dev/null 2>&1; then
  echo "  FAIL: livekit TURN over TLS rendered without a Secret, or on port 443"; fail=1
else
  echo "  OK: livekit TURN over TLS refuses a missing Secret and a privileged port"
fi
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set livekit.enabled=true >/dev/null 2>&1; then
  echo "  FAIL: livekit.enabled=true without livekit.url rendered"; fail=1
else
  echo "  OK: livekit.enabled=true without livekit.url is refused"
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
  --set flows.enabled=true --set flows.mail.enabled=true --set postgres.enabled=false \
  --set database.host=db.example.internal --set database.port=5432 \
  --set postgres.credentialsSecretName=external-pg-creds \
  --set agentApi.enabled=false --set terminal.enabled=false \
  --show-only templates/flows.yaml)"
if grep -A1 'name: DB_HOST' <<< "$FLOWS_EXT" | has 'value: "db.example.internal"' \
  && grep -A1 'name: DB_PORT' <<< "$FLOWS_EXT" | has 'value: "5432"'; then
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
  --set flows.enabled=true --set flows.mail.enabled=true --show-only templates/flows.yaml)"
if grep -A1 'name: DB_HOST' <<< "$FLOWS_DEFAULT" | has 'value: "vexa-vexa-postgres"' \
  && grep -A1 'name: DB_PORT' <<< "$FLOWS_DEFAULT" | has 'value: "5432"'; then
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
if grep -B2 'key: POSTGRES_USER' <<< "$FLOWS_EXT" | has 'name: "external-pg-creds"' \
  && grep -B2 'key: POSTGRES_PASSWORD' <<< "$FLOWS_EXT" | has 'name: "external-pg-creds"'; then
  echo "  OK: flows tier sources DB_USER/DB_PASSWORD via secretKeyRef against postgres.credentialsSecretName (#F-D5.1)"
else
  echo "  FAIL: flows tier does not source DB_USER/DB_PASSWORD via the resolved credentials Secret (#F-D5.1)"; fail=1
fi

# (2) sslmode threads from database.sslMode into every composed DSN. Flows only — admin-api and
#     meeting-api's own database modules have no DB_SSL_MODE/sslmode reader in current source (only
#     job-migrations sets that env var, and nothing appears to consume it there either), so adding
#     it to their Deployments would not be the one-line, provably-safe change threading it into
#     flows's own DSN query string is.
if grep -A1 'name: DB_SSL_MODE' <<< "$FLOWS_EXT" | has 'value: "disable"' \
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
if grep -B2 'key: POSTGRES_DB' <<< "$FLOWS_DEFAULT" | has 'name: "postgres-credentials"'; then
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
  --set flows.enabled=true --set flows.mail.enabled=true --set flows.databaseName=vexa --show-only templates/flows.yaml)"
if grep -A1 'name: FLOWS_DB_NAME' <<< "$FLOWS_SHARED_DB" | has 'value: "vexa"'; then
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
  --set adoptionPanel.enabled=true --set adoptionPanel.readerSecret=vexa-reporting \
  --show-only templates/adoption-panel.yaml)"
for want in "vexa-vexa-adoption-dashboard" "vexa-vexa-adoption-datasource" \
            "vexa-vexa-adoption-panel-env" "grafana_dashboard" "grafana_datasource" \
            "vexa-adoption-panel" "vexa-app-db" "vexa-flows-db" "reporting-role.sql" \
            "GRANT SELECT (created_at, event_type, subject_refs) ON public.reaction"; do
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

# GRAFANA NEVER LOGS IN AS THE OWNER ROLE. A Grafana Editor runs any SQL the datasource's role may,
# so the panel needs the SELECT-only reader's Secret named, and refuses the owner credentials.
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
     --set adoptionPanel.enabled=true --show-only templates/adoption-panel.yaml >/dev/null 2>&1; then
  echo "  FAIL: adoptionPanel renders with no reader Secret named"; fail=1
else
  echo "  OK: adoptionPanel refuses to render without adoptionPanel.readerSecret"
fi
OWNER_SECRET="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  | grep -B1 "key: POSTGRES_PASSWORD" | grep "name:" | sed -n 1p | sed -E 's/.*name: *"?([^" ]+)"?.*/\1/')"
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set adoptionPanel.enabled=true \
     --set "adoptionPanel.readerSecret=$OWNER_SECRET" --show-only templates/adoption-panel.yaml >/dev/null 2>&1; then
  echo "  FAIL: adoptionPanel renders wired to the owner credentials ($OWNER_SECRET)"; fail=1
else
  echo "  OK: adoptionPanel refuses the owner credentials as its reader ($OWNER_SECRET)"
fi

# The datasource follows PgBouncer when PgBouncer is on, like every other consumer in this
# chart. A reporting datasource opening its own direct connections past the pooler is how a
# dashboard becomes an incident.
POOLED="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set adoptionPanel.enabled=true --set adoptionPanel.readerSecret=vexa-reporting \
  --set pgbouncer.enabled=true --show-only templates/adoption-panel.yaml)"
if grep -A1 "VEXA_DB_HOST" <<< "$POOLED" | has "pgbouncer"; then
  echo "  OK: adoptionPanel datasource host follows pgbouncer when it is enabled"
else
  echo "  FAIL: adoptionPanel datasource bypasses pgbouncer"; fail=1
fi

# ── the flows tier is PUBLISHABLE: {repository, tag}, not a flat string ────────────────────────
# Until 2026-09-03 flows.image was ONE string ("vexaai/vexa-flows:dev"). Two consequences, both
# only visible from outside the repo:
#   (a) it was the single component that ignored global.imageTag, so a release-pinned render still
#       pulled a mutable :dev tag for the Minutes product;
#   (b) the channel publisher pins components by merging {<component>: {image: {tag: <ref>}}} over
#       values.yaml. Merging a map over a string REPLACES it — repository vanishes and the pin
#       cannot be expressed at all, which is why the enterprise station gate refused every chart
#       carrying flows as unpinned (S8) no matter what the publisher did.
# These four assertions are the shape check. Against the flat-string chart the first three fail.
FLOWS_PINNED="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set flows.enabled=true --set flows.mail.enabled=true --set global.imageTag=vPINNED --show-only templates/flows.yaml)"
flows_pinned_count="$(grep -cE '^\s+image: "vexaai/vexa-flows:vPINNED"$' <<< "$FLOWS_PINNED" || true)"
if [ "$flows_pinned_count" -eq 6 ]; then
  echo "  OK: all 6 flows containers follow global.imageTag ($flows_pinned_count)"
else
  echo "  FAIL: flows containers following global.imageTag — want 6 got $flows_pinned_count"; fail=1
fi
# The publisher's exact pin shape: `flows.image.tag` set to "<version>@sha256:<digest>". This is
# the addressability proof — a flat string has no .tag to set.
PIN_REF='v0.12.27@sha256:1111111111111111111111111111111111111111111111111111111111111111'
FLOWS_DIGEST="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set flows.enabled=true --set flows.mail.enabled=true --set "flows.image.tag=$PIN_REF" --show-only templates/flows.yaml)"
digest_count="$(grep -cE "^\s+image: \"vexaai/vexa-flows:${PIN_REF//./\\.}\"$" <<< "$FLOWS_DIGEST" || true)"
if [ "$digest_count" -eq 6 ]; then
  echo "  OK: publisher pin shape flows.image.tag=<version>@sha256 reaches all 6 containers ($digest_count)"
else
  echo "  FAIL: flows.image.tag digest pin reached $digest_count of 6 containers"; fail=1
fi
# The default (unpinned) render must name a repository and a tag, never a mutable :dev.
if grep -qE '^\s+image: "vexaai/vexa-flows:dev"$' <<< "$FLOWS_DEFAULT"; then
  echo "  FAIL: flows default image is still the mutable :dev tag"; fail=1
else
  echo "  OK: flows default image is not :dev"
fi
# With no tag set anywhere, every image defaults to the chart's own release, v<appVersion> — an
# immutable tag. A moving pointer (v012, latest) must never be the default an install pulls.
RELEASE_TAG="v$(awk -F'"' '/^appVersion:/ {print $2}' "$CHART/Chart.yaml")"
if grep -qE "^\s+image: \"vexaai/vexa-flows:${RELEASE_TAG//./\\.}\"$" <<< "$FLOWS_DEFAULT"; then
  echo "  OK: flows default image is repository:tag at the chart's release ($RELEASE_TAG)"
else
  echo "  FAIL: flows default image is not vexaai/vexa-flows:$RELEASE_TAG"; fail=1
fi
DEFAULT_RENDER="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true 2>&1)"
if grep -nE 'vexaai/vexa-[a-z-]+:(v012|latest)"?$' <<< "$DEFAULT_RENDER"; then
  echo "  FAIL: an image defaults to a moving tag (above)"; fail=1
else
  echo "  OK: no image defaults to a moving tag (v012, latest)"
fi
if [ "$(grep -cE "vexaai/vexa-[a-z-]+:${RELEASE_TAG//./\\.}\"?$" <<< "$DEFAULT_RENDER")" -ge 12 ]; then
  echo "  OK: the unpinned render pulls the chart's release tag"
else
  echo "  FAIL: the unpinned render does not pull $RELEASE_TAG"; fail=1
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
if grep -A1 'name: AGENT_API_URL' <<< "$GW_AGENT" | has 'value: "http://vexa-vexa-agent-api:8100"'; then
  echo "  OK: gateway names the agent-api Service under the default agentApi.enabled=true (#A2)"
else
  echo "  FAIL: gateway does not name the agent-api Service under the default agentApi.enabled=true (#A2)"; fail=1
fi

# A12.1 — VEXA_FLOWS_AGENT_API_URL was rendered as an EMPTY STRING exactly when the agent domain
# WAS enabled, and flows' `_is_set` reads "" as unset: the guard and the value were inverted, so
# every agent step answered not_present on an estate running agent-api. Value-level, both branches.
# Flows reaches agent-api only through the gateway's service door (its /agent prefix), never the
# agent-api Service itself.
if grep -A1 'name: VEXA_FLOWS_AGENT_API_URL' <<< "$FLOWS_DEFAULT" | has 'value: "http://vexa-vexa-gateway-door:8002/agent"'; then
  echo "  OK: flows names the gateway door's /agent when agentApi.enabled (not an empty string) (#A12)"
else
  echo "  FAIL: flows renders VEXA_FLOWS_AGENT_API_URL with no door address — unset by any reader (#A12)"; fail=1
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
  'VEXA_FLOWS_ADMIN_API_URL|value: "http://vexa-vexa-gateway-door:8002/identity"' \
  'VEXA_FLOWS_GATEWAY_URL|value: "http://vexa-vexa-gateway-door:8002/meetings"' \
  'VEXA_FLOWS_API_HOST|value: "0.0.0.0"' ; do
  k="${kv%%|*}"; v="${kv##*|}"
  if grep -A1 "name: $k" <<< "$FLOWS_API_ONLY" | has -F "$v"; then
    echo "  OK: flows-api carries $k = ${v#value: } (#A12)"
  else
    echo "  FAIL: flows-api is missing $k = ${v#value: } (#A12)"; fail=1
  fi
done
# Flows holds one credential, the flows service key, and no secret that can act as any person.
if grep -A4 'name: VEXA_FLOWS_SERVICE_KEY$' <<< "$FLOWS_API_ONLY" | has 'key: VEXA_FLOWS_SERVICE_KEY'; then
  echo "  OK: flows-api sources VEXA_FLOWS_SERVICE_KEY from the shared Secret (#A12)"
else
  echo "  FAIL: flows-api does not source VEXA_FLOWS_SERVICE_KEY (#A12)"; fail=1
fi
if grep -qE 'ADMIN_API_TOKEN|INTERNAL_API_SECRET|VEXA_FLOWS_ADMIN_KEY' <<< "$FLOWS_API_ONLY"; then
  echo "  FAIL: flows-api references ADMIN_API_TOKEN, INTERNAL_API_SECRET or VEXA_FLOWS_ADMIN_KEY"; fail=1
else
  echo "  OK: flows-api references neither ADMIN_API_TOKEN nor INTERNAL_API_SECRET"
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
  --set flows.enabled=true --set flows.mail.enabled=true --set global.imageTag=vPIN --show-only templates/flows.yaml)"
if grep -q 'image: "vexaai/vexa-flows:vPIN"' <<< "$FLOWS_PINNED" \
  && ! grep -q 'v012-flows:dev' <<< "$FLOWS_PINNED"; then
  echo "  OK: flows image honors global.imageTag (no mutable :dev left) (#A12)"
else
  echo "  FAIL: flows image ignored global.imageTag (#A12)"; fail=1
fi
FLOWS_OVERRIDE="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set flows.enabled=true --set flows.mail.enabled=true --set global.imageTag=vPIN \
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
  if grep -A1 "name: $k\$" <<< "$MA" | has -F "$v"; then echo "  OK: meeting-api $k = ${v#value: }"
  else echo "  FAIL: meeting-api $k is not ${v#value: }"; fail=1; fi
done
# Credentials only by secretKeyRef on storage.s3.existingSecret — never a literal value. Both name
# pairs (S3_* and the MINIO_* keys the /health object_storage row is declared on) read the Secret.
for k in S3_ACCESS_KEY MINIO_ACCESS_KEY; do
  if grep -A5 "name: $k\$" <<< "$MA" | tr -d '\n' | has -E 'secretKeyRef:[[:space:]]+name: "s3-fixture-credentials"[[:space:]]+key: "AWS_ACCESS_KEY_ID"'; then
    echo "  OK: $k from secretKeyRef s3-fixture-credentials/AWS_ACCESS_KEY_ID"
  else echo "  FAIL: $k is not read from the storage.s3 Secret"; fail=1; fi
done
for k in S3_SECRET_KEY MINIO_SECRET_KEY; do
  if grep -A5 "name: $k\$" <<< "$MA" | tr -d '\n' | has -E 'secretKeyRef:[[:space:]]+name: "s3-fixture-credentials"[[:space:]]+key: "AWS_SECRET_ACCESS_KEY"'; then
    echo "  OK: $k from secretKeyRef s3-fixture-credentials/AWS_SECRET_ACCESS_KEY"
  else echo "  FAIL: $k is not read from the storage.s3 Secret"; fail=1; fi
done
if grep -A1 -E 'name: (S3|MINIO)_(ACCESS|SECRET)_KEY$' <<< "$MA" | has 'value:'; then
  echo "  FAIL: a storage credential is rendered as a literal value"; fail=1
else echo "  OK: no storage credential rendered as a literal value"; fi
# People's photos: admin-api signs with its OWN key (adminApi.photos.existingSecret), never the
# recordings key and never a root pair. Unset ⇒ no photos and no storage credential on admin-api;
# named ⇒ only that Secret; the recordings key Secret named there is refused at render.
AD_OFF="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-admin-api.yaml)"
if ! grep -qE 'name: (S3|MINIO|VEXA_PHOTOS_S3|ROOT)_(ACCESS|SECRET)_KEY$' <<< "$AD_OFF" && ! grep -q 's3-fixture-credentials' <<< "$AD_OFF"; then
  echo "  OK: admin-api holds no storage credential when no photos key is named"
else echo "  FAIL: admin-api holds a storage credential without adminApi.photos.existingSecret"; fail=1; fi
AD_ON="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set adminApi.photos.existingSecret=vexa-photos-s3 --show-only templates/deployment-admin-api.yaml)"
if grep -A5 'name: VEXA_PHOTOS_S3_ACCESS_KEY$' <<< "$AD_ON" | tr -d '\n' | has -E 'secretKeyRef:[[:space:]]+name: "vexa-photos-s3"[[:space:]]+key: "AWS_ACCESS_KEY_ID"' \
   && grep -A5 'name: VEXA_PHOTOS_S3_SECRET_KEY$' <<< "$AD_ON" | tr -d '\n' | has -E 'secretKeyRef:[[:space:]]+name: "vexa-photos-s3"[[:space:]]+key: "AWS_SECRET_ACCESS_KEY"' \
   && grep -A1 'name: MINIO_BUCKET$' <<< "$AD_ON" | has -F 'value: "vexa-recordings"' \
   && ! grep -qE 'name: (S3|MINIO|ROOT)_(ACCESS|SECRET)_KEY$' <<< "$AD_ON" && ! grep -q 's3-fixture-credentials' <<< "$AD_ON"; then
  echo "  OK: admin-api reads its photos key from adminApi.photos.existingSecret, in the recordings bucket, and no recordings or root key"
else echo "  FAIL: admin-api's photos wiring is not its own key"; fail=1; fi
if ! helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set adminApi.photos.existingSecret=s3-fixture-credentials >/dev/null 2>&1; then
  echo "  OK: rendering refuses the recordings key Secret as admin-api's photos key"
else echo "  FAIL: adminApi.photos.existingSecret may name the recordings key"; fail=1; fi
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
if grep -q 'ca_bundle = /etc/vexa/s3-ca/ca.crt' <<< "$CA" && grep -A6 'name: s3-ca$' <<< "$CA" | has 'name: "corp-ca"' \
   && grep -q 'key: "ca-bundle.crt"' <<< "$CA" && grep -q 'mountPath: /etc/vexa/s3-ca' <<< "$CA"; then
  echo "  OK: caBundle.configMapName mounts the bundle and names it in the client config"
else echo "  FAIL: caBundle.configMapName not wired"; fail=1; fi
CAS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set storage.s3.caBundle.secretName=corp-ca \
  --show-only templates/deployment-meeting-api.yaml)"
if grep -A3 'name: s3-ca$' <<< "$CAS" | has 'secretName: "corp-ca"'; then
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
  openssl pkey -in "$tmp/k" -noout -text 2>/dev/null | has 'ED25519 Private-Key' || { rm -rf "$tmp"; return 1; }
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
if grep -A1 'name: MCP_URL$' <<< "$RENDER" | has 'value: "http://vexa-vexa-mcp:8010"'; then
  echo "  OK: the gateway relays /mcp to the chart's MCP service"
else
  echo "  FAIL: gateway MCP_URL does not name vexa-vexa-mcp:8010"; fail=1
fi
if grep -A1 'name: VEXA_MCP_URL$' <<< "$RENDER" | has 'value: "http://vexa-vexa-gateway:8000/mcp"'; then
  echo "  OK: agent-api's worker toolbelt points at the gateway's /mcp"
else
  echo "  FAIL: agent-api VEXA_MCP_URL does not name the gateway's /mcp"; fail=1
fi
if grep -A1 'name: AGENT_API_URL$' <<< "$(awk '/deployment-mcp.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER")" \
    | has 'vexa-vexa-agent-api:8100'; then
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
if grep -q 'runtime.managed: "true"' <<< "$np_meeting" && grep -A2 'key: vexa.role' <<< "$np_meeting" | has 'NotIn'; then
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
# The chart's redis requires a password; each of its six clients expands it into its URL.
redis_dep="$(awk '/deployment-redis.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER")"
if grep -q -- '- "--requirepass"' <<< "$redis_dep" && grep -A1 -- '- "--requirepass"' <<< "$redis_dep" | has '\$(REDIS_PASSWORD)'; then
  echo "  OK: redis requires a password"
else echo "  FAIL: redis does not require a password"; fail=1; fi
n_urls="$(grep -cE 'value: "redis://:\$\(REDIS_PASSWORD\)@vexa-vexa-redis' <<< "$RENDER" || true)"
if [ "$n_urls" = 6 ]; then echo "  OK: the six redis clients carry the password in their URL"
else echo "  FAIL: $n_urls redis URL(s) carry the password, want 6"; fail=1; fi
if grep -qE 'value: "redis://vexa-vexa-redis' <<< "$RENDER"; then
  echo "  FAIL: a redis URL without the password is rendered"; fail=1
else echo "  OK: no password-less redis URL"; fi
for f in deployment-admin-api deployment-agent-api deployment-gateway deployment-meeting-api deployment-runtime; do
  block="$(awk "/$f.yaml/{f=1} f{print} f&&/^---/{exit}" <<< "$RENDER")"
  pw_line="$(grep -n 'name: REDIS_PASSWORD$' <<< "$block" | sed -n 1p | cut -d: -f1)"
  url_line="$(grep -nE 'name: (VEXA_)?REDIS_URL$' <<< "$block" | sed -n 1p | cut -d: -f1)"
  if [ -n "$pw_line" ] && [ -n "$url_line" ] && [ "$pw_line" -lt "$url_line" ]; then :
  else echo "  FAIL: $f does not define REDIS_PASSWORD before its redis URL"; fail=1; fi
done
echo "  OK: every redis client defines REDIS_PASSWORD before the URL that expands it"
refuse_redis="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set secrets.redisPassword=short 2>&1 || true)"
if grep -q 'secrets.redisPassword must be 32+' <<< "$refuse_redis"; then echo "  OK: a short secrets.redisPassword is refused"
else echo "  FAIL: a short secrets.redisPassword rendered"; fail=1; fi
if grep -A1 'name: INTERNAL_API_SECRET$' <<< "$(awk '/deployment-runtime.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER")" | has secretKeyRef; then
  echo "  FAIL: the runtime still carries INTERNAL_API_SECRET"; fail=1
else echo "  OK: the runtime carries no internal-tier secret"; fi

refuse() {  # refuse <label> <expected-message-regex> <helm args...>
  local label="$1" want="$2" out; shift 2
  if out="$(helm template vexa "$CHART" -n vexa "$@" 2>&1)"; then
    echo "  FAIL: $label — rendered, must refuse"; fail=1
  elif grep -qE "$want" <<< "$out"; then echo "  OK: $label → refused with the actionable message"
  else echo "  FAIL: $label — refused without the expected message: $(grep -m1 Error <<< "$out")"; fail=1; fi
}
# ── calendar.internalFeedAllow / calendar.autoJoinBlock — operator calendar settings ────────────
# Empty by default (no internal feed, no deployment block); a set value reaches exactly the services
# that read it: the feed allowance admin-api (save) and meeting-api (fetch), the block list meeting-api.
cal_env() {  # cal_env <template> <key> [helm args…] → the value line under that env name
  local t="$1" k="$2"; shift 2
  helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" -s "templates/$t" "$@" \
    | grep -A1 "name: $k\$" | grep 'value:' | sed -n 1p
}
for t in deployment-admin-api.yaml deployment-meeting-api.yaml; do
  if [ "$(cal_env $t VEXA_CALENDAR_FEED_ALLOW)" = '              value: ""' ]; then
    echo "  OK: $t VEXA_CALENDAR_FEED_ALLOW empty by default"
  else echo "  FAIL: $t VEXA_CALENDAR_FEED_ALLOW not empty by default"; fail=1; fi
  if cal_env $t VEXA_CALENDAR_FEED_ALLOW --set 'calendar.internalFeedAllow=cal.corp.example\,10.20.0.0/16' \
       | has 'value: "cal.corp.example,10.20.0.0/16"'; then
    echo "  OK: $t carries calendar.internalFeedAllow"
  else echo "  FAIL: $t ignores calendar.internalFeedAllow"; fail=1; fi
done
if cal_env deployment-meeting-api.yaml VEXA_AUTO_JOIN_BLOCK --set calendar.autoJoinBlock=gated.example \
     | has 'value: "gated.example"' \
   && [ "$(cal_env deployment-meeting-api.yaml VEXA_AUTO_JOIN_BLOCK)" = '              value: ""' ]; then
  echo "  OK: meeting-api VEXA_AUTO_JOIN_BLOCK empty by default, carries calendar.autoJoinBlock"
else echo "  FAIL: meeting-api VEXA_AUTO_JOIN_BLOCK plumbing"; fail=1; fi
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" | has 'VEXA_CALENDAR_FEED_ALLOW' \
   && ! helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" -s templates/deployment-gateway.yaml \
        | has 'VEXA_CALENDAR_FEED_ALLOW'; then
  echo "  OK: the feed allowance reaches no other service (gateway checked)"
else echo "  FAIL: feed allowance placement"; fail=1; fi

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
    | sed -nE 's/^  NEXTAUTH_SECRET: "(.*)"$/\1/p' | sed -n 1p
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
    | sed -nE 's/^  VEXA_DISPATCH_SIGNING_KEY: "(.*)"$/\1/p' | sed -n 1p
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
    | sed -nE 's/^  ADMIN_API_TOKEN: "(.*)"$/\1/p' | sed -n 1p
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
# The broker renders with agent-api, admits only agent-api, terminal and (bot accounts on)
# meeting-api Pods, and each consumer mounts only its own role key: agent-api never the human key,
# the terminal never the agent key, meeting-api only the bots key.
echo "=== credential broker ==="
need 1 'name: vexa-vexa-credential-broker$'        "broker Deployment/Service name"
need 1 '^kind: NetworkPolicy'                      "broker NetworkPolicy"
need 1 'name: vexa-vexa-credential-broker-state'   "broker state PVC"
need 1 'helm.sh/resource-policy: keep'             "broker keys Secret kept across uninstall"
need 2 'name: VEXA_CONNECTIONS_BROKER_URL'         "broker URL on agent-api AND terminal"
need 1 'name: VEXA_CONNECTIONS_AGENT_KEY_FILE'     "agent role key path"
need 1 'name: VEXA_CONNECTIONS_HUMAN_KEY_FILE'     "human role key path"
# 5 component labels: the policy's own two (metadata + podSelector) and the three admitted sources.
NP="$(awk '/Source: vexa\/templates\/networkpolicy-credential-broker.yaml/,/^---/' <<< "$RENDER")"
if grep -q 'component: agent-api' <<< "$NP" && grep -q 'component: terminal' <<< "$NP" \
   && grep -q 'component: meeting-api' <<< "$NP" \
   && [ "$(grep -c 'component: ' <<< "$NP")" -eq 5 ]; then echo "  OK: NetworkPolicy admits exactly agent-api + terminal + meeting-api"
else echo "  FAIL: NetworkPolicy ingress is not exactly agent-api + terminal + meeting-api"; fail=1; fi
MAPI="$(awk '/Source: vexa\/templates\/deployment-meeting-api.yaml/,/^---/' <<< "$RENDER")"
if grep -q 'key: bots.key, path: bots/key' <<< "$MAPI" && ! grep -qE 'key: (agent|human|git|store).key' <<< "$MAPI" \
   && grep -q 'name: VEXA_BOT_ACCOUNTS_BROKER_URL' <<< "$MAPI" && grep -q 'name: VEXA_BOT_ACCOUNTS_KEY_FILE' <<< "$MAPI" \
   && grep -q 'name: VEXA_CONNECTIONS_BOTS_KEY_FILE' <<< "$RENDER"; then
  echo "  OK: meeting-api mounts the bots key only, and the broker reads it"
else echo "  FAIL: bot-account key wiring"; fail=1; fi
NOBOTS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set credentialBroker.botAccounts=false)"
NOBOTS_NP="$(awk '/Source: vexa\/templates\/networkpolicy-credential-broker.yaml/,/^---/' <<< "$NOBOTS")"
if ! grep -qE 'VEXA_BOT_ACCOUNTS_|VEXA_CONNECTIONS_BOTS_KEY_FILE|bots.key, path' <<< "$NOBOTS" \
   && ! grep -q 'component: meeting-api' <<< "$NOBOTS_NP"; then echo "  OK: botAccounts=false renders no bots role"
else echo "  FAIL: botAccounts=false still wires the bots role"; fail=1; fi
AGENT="$(awk '/Source: vexa\/templates\/deployment-agent-api.yaml/,/^---/' <<< "$RENDER")"
TERM="$(awk '/Source: vexa\/templates\/deployment-terminal.yaml/,/^---/' <<< "$RENDER")"
if grep -q 'key: agent.key' <<< "$AGENT" && grep -q 'key: git.key, path: git/key' <<< "$AGENT" \
   && ! grep -qE 'key: (human|store).key' <<< "$AGENT" && [ "$(grep -c 'name: VEXA_GIT_STORE_BROKER_URL' <<< "$AGENT")" -eq 1 ]; then
  echo "  OK: agent-api mounts the agent and git keys only, and keeps Git credentials in the broker"
else echo "  FAIL: agent-api key mounts wrong"; fail=1; fi
if grep -q 'key: human.key' <<< "$TERM" && ! grep -qE 'key: (agent|git|store).key' <<< "$TERM"; then
  echo "  OK: terminal mounts the human key only"
else echo "  FAIL: terminal key mounts wrong"; fail=1; fi
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
# ── terminal.canvasHost — the Meeting Canvas origin (docs/docs/meeting-canvas-isolation.mdx) ────────
# Unset (the default): no canvas origin is named and the ingress has no second host; the terminal then
# serves the frame itself and warns at start. Set: the terminal is told the origin (publicUrl's scheme),
# and the ingress routes that host to the terminal for the frame document and the build's files only.
if ! grep -q 'TERMINAL_CANVAS_ORIGIN' <<< "$RENDER"; then echo "  OK: no canvas origin by default"
else echo "  FAIL: a canvas origin is rendered by default"; fail=1; fi
CANVAS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set terminal.publicUrl=https://app.example.test \
  --set terminal.canvasHost=canvas.example.test --set ingress.enabled=true --set ingress.host=app.example.test)"
canvas_ing="$(awk '/^# Source: vexa\/templates\/ingress.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$CANVAS")"
canvas_rule="$(awk '/- host: "canvas.example.test"/{f=1} f{print}' <<< "$canvas_ing")"
if grep -A1 'name: TERMINAL_CANVAS_ORIGIN$' <<< "$CANVAS" | has 'value: "https://canvas.example.test"'; then
  echo "  OK: terminal.canvasHost names the canvas origin on the terminal, with publicUrl's scheme"
else echo "  FAIL: terminal.canvasHost does not reach TERMINAL_CANVAS_ORIGIN"; fail=1; fi
if grep -q 'path: /canvas-frame' <<< "$canvas_rule" && grep -A1 'path: /canvas-frame' <<< "$canvas_rule" | has 'pathType: Exact' \
   && grep -A1 'path: /_next/static$' <<< "$canvas_rule" | has 'pathType: Prefix' \
   && [ "$(grep -c 'path: ' <<< "$canvas_rule")" -eq 2 ] && grep -q 'name: vexa-vexa-terminal' <<< "$canvas_rule"; then
  echo "  OK: the ingress routes the canvas host to the terminal for /canvas-frame and /_next/static alone"
else echo "  FAIL: the canvas host's ingress rule is not exactly /canvas-frame + /_next/static to the terminal"; fail=1; fi
refuse "a canvas host without the terminal's public origin" 'terminal\.canvasHost needs terminal\.publicUrl' \
  -f "$CHART/values-test.yaml" --set terminal.canvasHost=canvas.example.test
refuse "a canvas host that is not a host name" 'terminal\.canvasHost must be a host name' \
  -f "$CHART/values-test.yaml" --set terminal.publicUrl=https://app.example.test --set 'terminal.canvasHost=https://canvas.example.test/x'
EXT="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set credentialBroker.existingSecret=my-keys)"
if ! grep -q 'name: vexa-vexa-credential-broker-keys' <<< "$EXT" && [ "$(grep -c 'secretName: my-keys' <<< "$EXT")" -eq 4 ]; then
  echo "  OK: existingSecret replaces the generated keys on all four consumers"
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
if grep -A1 'name: RUNTIME_EXTRA_MOUNT_SOURCES$' <<< "$rt_block" | has 'value: ""'; then :
else echo "  FAIL: the runtime serves an out-of-store mount source nobody configured"; fail=1; fi
glob_render="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set agentApi.globalSystemWorkspacePath=/srv/global)"
glob_rt="$(awk '/deployment-runtime.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$glob_render")"
if grep -A1 'name: RUNTIME_EXTRA_MOUNT_SOURCES$' <<< "$glob_rt" | has 'value: "/srv/global"'; then
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
NP_FLOWS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --set flows.mail.enabled=true \
  | awk 'BEGIN{RS="\n---\n"} /kind: NetworkPolicy/')"
loose="$(awk '{ buf[NR%9]=$0 }
  /values: \[flows-worker|values: \["flows-worker"|component: flows-api$/ {
    ok=0; for (i=1;i<9;i++) if (buf[(NR-i)%9] ~ /app.kubernetes.io\/instance: vexa/) ok=1
    if (!ok) n++ }
  END { print n+0 }' <<< "$NP_FLOWS")"
pod_labels="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --set flows.mail.enabled=true --show-only templates/flows.yaml \
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
if grep -A1 'resources: \["secrets"\]' <<< "$role" | has 'verbs: \["create"\]$' \
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
# agent-api holds the database's credentials since ADR-0048 (its session store), so it is listed.
if grep -q 'values: \[admin-api, meeting-api, agent-api, pgbouncer, migrations\]' <<< "$pg" \
   && ! grep -qE 'runtime.managed|component: (gateway|runtime|mcp|terminal)$' <<< "$pg"; then
  echo "  OK: only the database's credential holders reach postgres"
else echo "  FAIL: postgres admits more than its credential holders"; fail=1; fi
cpe="$(np_doc vexa-vexa-control-plane-egress)"
if grep -q 'values:' <<< "$cpe" && grep -A3 'operator: NotIn' <<< "$cpe" | has -- '- redis' \
   && grep -A1 'except:' <<< "$cpe" | has -- '- 169.254.0.0/16'; then
  echo "  OK: control-plane egress leaves out postgres and redis and the metadata range"
else echo "  FAIL: control-plane egress"; fail=1; fi
if grep -q -- '- 10.0.0.0/8' <<< "$cpe"; then echo "  FAIL: control-plane egress refuses private ranges by default"; fail=1; fi
off="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set networkPolicy.defaultDeny.enabled=false)"
if grep -q 'name: vexa-vexa-default-deny' <<< "$off"; then echo "  FAIL: defaultDeny.enabled=false still renders the default-deny"; fail=1
else echo "  OK: networkPolicy.defaultDeny.enabled=false renders no default-deny"; fi
fl_render="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --set flows.mail.enabled=true)"
fl="$(awk '/^  name: vexa-vexa-flows-api-ingress$/{f=1} f{print} f&&/^---/{exit}' <<< "$fl_render")"
if grep -q 'vexa.role: worker' <<< "$fl"; then echo "  OK: flows-api admits the chart's Pods and agent workers"
else echo "  FAIL: flows-api ingress under the default-deny"; fail=1; fi

# The broker answers readiness on /ready (503 while its store does not), liveness on /health.
broker="$(awk '/deployment-credential-broker.yaml/{f=1} f{print} f&&/^---/{exit}' <<< "$RENDER")"
if grep -A2 'readinessProbe:' <<< "$broker" | has 'path: /ready'; then echo "  OK: the broker's readiness is /ready"
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
# flows.apiKey is empty. Then the chart's flows Secret omits the key and all seven consumers (three
# flows containers, the MCP edge, agent-api, and the publish edges of meeting-api and admin-api) read
# it from that Secret by name. With flows.apiKey set, nothing
# changes; both apiKey and existingSecret is refused.
flows_key_refs() {  # flows_key_refs <render> → "<secret> <count>" lines for VEXA_FLOWS_API_KEY refs
  grep -A4 -E '^[[:space:]]+- name: VEXA_FLOWS_API_KEY$' <<< "$1" | grep 'key: VEXA_FLOWS_API_KEY' -B1 \
    | sed -nE 's/^[[:space:]]+name: "?([^"]*)"?$/\1/p' | sort | uniq -c | awk '{print $2" "$1}'
}
for src in "secrets.existingSecretName=shared-secrets shared-secrets" "flows.existingSecret=flows-key flows-key"; do
  set -- $src
  fk="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --set flows.mail.enabled=true --set "$1")"
  if grep -qE '^  VEXA_FLOWS_API_KEY:' <<< "$fk"; then echo "  FAIL: $1 still renders the key into the flows Secret"; fail=1
  elif [ "$(flows_key_refs "$fk")" = "$2 7" ]; then echo "  OK: $1 — flows' three containers, the MCP edge, agent-api, meeting-api and admin-api read VEXA_FLOWS_API_KEY from $2"
  else echo "  FAIL: $1 — VEXA_FLOWS_API_KEY refs: $(flows_key_refs "$fk" | tr '\n' ';')"; fail=1; fi
done
fk="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --set flows.mail.enabled=true \
  --set secrets.existingSecretName=shared-secrets --set flows.apiKey=inline-test-key)"
if grep -qE '^  VEXA_FLOWS_API_KEY: "inline-test-key"$' <<< "$fk" && [ "$(flows_key_refs "$fk")" = "vexa-vexa-flows 4" ]; then
  echo "  OK: flows.apiKey in values keeps the key in the chart's flows Secret, as before"
else echo "  FAIL: flows.apiKey with secrets.existingSecretName"; fail=1; fi
fboth="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --set flows.mail.enabled=true \
  --set flows.existingSecret=flows-key --set flows.apiKey=inline-test-key 2>&1 || true)"
if grep -q 'flows.apiKey and flows.existingSecret are both set' <<< "$fboth"; then echo "  OK: flows.apiKey with flows.existingSecret is refused"
else echo "  FAIL: flows.apiKey with flows.existingSecret rendered"; fail=1; fi

# The model catalog (ADR-0043): absent by default; when set, agent-api carries it as one JSON env var
# and each provider secret it references by env:VEXA_MODEL_SECRET_<NAME> from a Secret, never as a value.
if grep -q 'name: VEXA_MODEL_CATALOG' <<< "$RENDER"; then echo "  FAIL: a model catalog rendered with none set"; fail=1
else echo "  OK: no model catalog unless one is set"; fi
mc="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set-json 'models.catalog={"providers":{"openrouter":{"adapter":"openrouter","auth":"secret","secret_ref":"env:VEXA_MODEL_SECRET_OPENROUTER"}},"models":[{"id":"or-sonnet","display_name":"Sonnet","provider":"openrouter","model":"anthropic/claude-sonnet-4.5"}]}' \
  --set-json 'models.catalogSecrets=[{"env":"VEXA_MODEL_SECRET_OPENROUTER","secretName":"model-keys","key":"openrouter"}]')"
mc_json="$(grep -A1 -E '^[[:space:]]+- name: VEXA_MODEL_CATALOG$' <<< "$mc" | sed -nE 's/^[[:space:]]+value: (.*)$/\1/p')"
if python3 -c 'import json,sys; d=json.loads(json.loads(sys.argv[1])); assert d["models"][0]["id"]=="or-sonnet"' "$mc_json" 2>/dev/null; then
  echo "  OK: models.catalog renders as VEXA_MODEL_CATALOG JSON on agent-api"
else echo "  FAIL: models.catalog did not render as JSON"; fail=1; fi
if grep -A4 -E '^[[:space:]]+- name: VEXA_MODEL_SECRET_OPENROUTER$' <<< "$mc" | has 'name: model-keys' \
   && grep -A5 -E '^[[:space:]]+- name: VEXA_MODEL_SECRET_OPENROUTER$' <<< "$mc" | has 'key: openrouter'; then
  echo "  OK: models.catalogSecrets delivers each secret_ref from its Secret"
else echo "  FAIL: models.catalogSecrets"; fail=1; fi
# A catalog secret is VEXA_MODEL_SECRET_<NAME> from a Secret the operator names (R1797-1): never one of
# agent-api's own variable names, never the chart's shared admin-token Secret by default.
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
     --set-json 'models.catalogSecrets=[{"env":"INTERNAL_API_SECRET","secretName":"model-keys"}]' >/dev/null 2>&1; then
  echo "  FAIL: a catalog secret named like agent-api's own variable rendered"; fail=1
else echo "  OK: a catalog secret outside VEXA_MODEL_SECRET_* is refused"; fi
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
     --set-json 'models.catalogSecrets=[{"env":"VEXA_MODEL_SECRET_OPENROUTER"}]' >/dev/null 2>&1; then
  echo "  FAIL: a catalog secret rendered with no secretName"; fail=1
else echo "  OK: a catalog secret needs its own named Secret"; fi
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
flows_np="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --set flows.mail.enabled=true)"
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
else echo "  FAIL: flows matched by component alone:"; echo "$loose" | sed -n 1,40p; fail=1; fi

# agentApi.gitProviders reaches agent-api as VEXA_GIT_PROVIDERS, verbatim; unset renders empty (every
# host keeps GitHub's token form).
git_hosts="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" \
  --set-string agentApi.gitProviders='git.example.com=gitlab\,code.example.org=basic:svc-vexa' \
  | grep -A1 'name: VEXA_GIT_PROVIDERS' | grep 'value:' || true)"
git_default="$(printf '%s\n' "$RENDER" | grep -A1 'name: VEXA_GIT_PROVIDERS' | grep 'value:' || true)"
if grep -q '"git.example.com=gitlab,code.example.org=basic:svc-vexa"' <<< "$git_hosts" \
   && grep -q 'value: ""' <<< "$git_default"; then
  echo "  OK: agentApi.gitProviders reaches agent-api as VEXA_GIT_PROVIDERS (empty by default)"
else echo "  FAIL: VEXA_GIT_PROVIDERS not rendered as set: [$git_hosts] default [$git_default]"; fail=1; fi

# A list of providers (the git provider port) renders as one JSON value, private CA included.
GP_VALUES="$(mktemp)"
cat > "$GP_VALUES" <<'YAML'
agentApi:
  gitProviders:
    - name: corp
      kind: gitlab
      baseUrl: https://git.example.com:8443/gitlab
      label: Company GitLab
      caBundle: |
        -----BEGIN CERTIFICATE-----
        MIIBtest
        -----END CERTIFICATE-----
YAML
git_list="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" -f "$GP_VALUES" \
  | grep -A1 'name: VEXA_GIT_PROVIDERS' | grep 'value:' || true)"
rm -f "$GP_VALUES"
if grep -qF '\"name\":\"corp\"' <<< "$git_list" && grep -qF '\"kind\":\"gitlab\"' <<< "$git_list" \
   && grep -qF '\"baseUrl\":\"https://git.example.com:8443/gitlab\"' <<< "$git_list" \
   && grep -qF -- '-----BEGIN CERTIFICATE-----\\nMIIBtest' <<< "$git_list"; then
  echo "  OK: a gitProviders list reaches agent-api as one JSON VEXA_GIT_PROVIDERS"
else echo "  FAIL: gitProviders list not rendered as JSON: [$git_list]"; fail=1; fi

# ── the bot mailbox against an on-premises Exchange (flows.mail) ───────────────────────────────
# Before: IMAP was imap.gmail.com in code, the chart had no IMAP or relay values for flows, the
# mailbox password could only sit in values, and the mailbox rendered (and restart-looped) with no
# address at all. Each assertion below fails on that chart.
MAIL_VALUES="$(mktemp)"
cat > "$MAIL_VALUES" <<'YAML'
flows:
  enabled: true
  mail:
    address: bot@corp.example
    existingSecret: mail-creds
    imap: {host: mail.corp.example, tls: starttls, user: 'CORP\vexa-bot', folder: Invites}
    authservId: mail.corp.example
    smtp: {host: smtp.corp.example, port: "587", tls: starttls, user: 'CORP\vexa-bot'}
    ca: {configMapName: corp-ca, key: ca-bundle.pem}
YAML
MAIL_R="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" -f "$MAIL_VALUES" --show-only templates/flows.yaml)"
MB="$(awk '/^---/{p=0} /name: vexa-vexa-flows-mailbox$/{p=1} p' <<< "$MAIL_R")"
mail_ok=1
for kv in 'VEXA_MAIL_IMAP_HOST|value: "mail.corp.example"' 'VEXA_MAIL_IMAP_TLS|value: "starttls"' \
          'VEXA_MAIL_IMAP_USER|value: "CORP\\vexa-bot"' 'VEXA_MAIL_IMAP_FOLDER|value: "Invites"' \
          'VEXA_FLOWS_MAIL_AUTHSERV_ID|value: "mail.corp.example"' \
          'VEXA_MAIL_SMTP_HOST|value: "smtp.corp.example"' 'VEXA_MAIL_SMTP_TLS|value: "starttls"' \
          'VEXA_MAIL_IMAP_CA_FILE|value: "/etc/vexa/mail-ca/ca.pem"' \
          'VEXA_MAIL_SMTP_CA_FILE|value: "/etc/vexa/mail-ca/ca.pem"'; do
  k="${kv%%|*}"; v="${kv#*|}"
  grep -A1 "name: $k\$" <<< "$MB" | has -F "$v" || { echo "  FAIL: flows-mailbox $k is not $v"; mail_ok=0; }
done
if grep -A4 'name: VEXA_MAIL_APP_PASSWORD$' <<< "$MB" | tr -d '\n' | has -E 'name: "mail-creds"[[:space:]]+key: VEXA_MAIL_APP_PASSWORD'; then :
else echo "  FAIL: flows-mailbox does not read VEXA_MAIL_APP_PASSWORD from flows.mail.existingSecret"; mail_ok=0; fi
if awk '/^---/{p=0} /kind: Secret/{p=1} p' <<< "$MAIL_R" | has 'VEXA_MAIL_APP_PASSWORD'; then
  echo "  FAIL: the chart's flows Secret still renders VEXA_MAIL_APP_PASSWORD under flows.mail.existingSecret"; mail_ok=0; fi
grep -qE 'name: "corp-ca"' <<< "$MB" && grep -qE 'key: "ca-bundle.pem"' <<< "$MB" \
  || { echo "  FAIL: the CA bundle ConfigMap is not mounted into flows-mailbox"; mail_ok=0; }
grep -q 'flows_integrations.mailbox_status' <<< "$MB" \
  || { echo "  FAIL: flows-mailbox carries no readiness probe over its own poll report"; mail_ok=0; }
grep -qE 'mountPath: /tmp$' <<< "$MB" || { echo "  FAIL: flows-mailbox has no writable /tmp for its report"; mail_ok=0; }
[ "$mail_ok" -eq 1 ] && echo "  OK: flows.mail renders IMAP/SMTP endpoints, TLS modes, the CA bundle, the existing-Secret password and the mailbox probe"
[ "$mail_ok" -eq 1 ] || fail=1
rm -f "$MAIL_VALUES"
# The relay is the deployment's: with no flows.mail.smtp.host, flows sends through terminal.mail.
RELAY="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
  --set flows.mail.address=bot@corp.example --set terminal.mail.smtpHost=relay.corp.example \
  --set terminal.mail.smtpPort=587 --show-only templates/flows.yaml)"
if grep -A1 'name: VEXA_MAIL_SMTP_HOST$' <<< "$RELAY" | has -F 'value: "relay.corp.example"'; then
  echo "  OK: flows inherits the deployment relay from terminal.mail when flows.mail.smtp.host is empty"
else echo "  FAIL: flows does not inherit terminal.mail's relay"; fail=1; fi
# Off unless it can run; an explicit false wins over an address; both passwords refused.
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
     --show-only templates/flows.yaml | has 'name: vexa-vexa-flows-mailbox$'; then
  echo "  FAIL: flows-mailbox renders with no address (it restart-loops on its first login)"; fail=1
else echo "  OK: flows-mailbox does not render without an address or flows.mail.enabled"; fi
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
     --set flows.mail.address=bot@corp.example --set flows.mail.enabled=false \
     --show-only templates/flows.yaml | has 'name: vexa-vexa-flows-mailbox$'; then
  echo "  FAIL: flows.mail.enabled=false did not turn the mailbox off"; fail=1
else echo "  OK: flows.mail.enabled=false turns the mailbox off even with an address"; fi
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
     --set flows.mail.appPassword=x --set flows.mail.existingSecret=mail-creds >/dev/null 2>&1; then
  echo "  FAIL: flows.mail.appPassword with flows.mail.existingSecret was not refused"; fail=1
else echo "  OK: flows.mail.appPassword with flows.mail.existingSecret is refused"; fi
if helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
     --set flows.mail.ca.configMapName=a --set flows.mail.ca.secretName=b >/dev/null 2>&1; then
  echo "  FAIL: flows.mail.ca with both a ConfigMap and a Secret was not refused"; fail=1
else echo "  OK: flows.mail.ca with both a ConfigMap and a Secret is refused"; fi

# ── diagnostics.captureSignal — no captured-signal tape by default on Kubernetes ─────────────────
# Both services that decide whether a bot tapes (admin-api's last tier, meeting-api's no-answer
# fallback) get VEXA_CAPTURE_SIGNAL_DEFAULT="false" from the chart defaults; "true" turns it on; any
# other value stops the render.
for t in deployment-admin-api.yaml deployment-meeting-api.yaml; do
  doc="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" -s "templates/$t")"
  if grep -A1 'name: VEXA_CAPTURE_SIGNAL_DEFAULT$' <<< "$doc" | has 'value: "false"'; then
    echo "  OK: $t sets VEXA_CAPTURE_SIGNAL_DEFAULT=false by default (no tapes)"
  else echo "  FAIL: $t does not default VEXA_CAPTURE_SIGNAL_DEFAULT to false"; fail=1; fi
  doc="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" -s "templates/$t" --set diagnostics.captureSignal=true)"
  if grep -A1 'name: VEXA_CAPTURE_SIGNAL_DEFAULT$' <<< "$doc" | has 'value: "true"'; then
    echo "  OK: $t carries diagnostics.captureSignal=true when the operator turns tapes on"
  else echo "  FAIL: $t ignores diagnostics.captureSignal=true"; fail=1; fi
done
refuse "diagnostics.captureSignal not a boolean" 'diagnostics\.captureSignal must be "true" or "false" \(got "maybe"\)' \
  -f "$CHART/values-test.yaml" --set diagnostics.captureSignal=maybe

# The deployment's default transcription language reaches meeting-api as the two env vars its boot
# check reads (transcription-language.v1 DeploymentDefault): empty by default, and a value list
# joined with commas and no spaces, which is the only list form the contract accepts.
lang_env() { awk -v n="$2" '$0 ~ "- name: "n"$" { getline; sub(/^ *value: /, ""); print; exit }' <<< "$1"; }
if [ "$(lang_env "$RENDER" DEFAULT_TRANSCRIPTION_LANGUAGE)" = '""' ] \
   && [ "$(lang_env "$RENDER" DEFAULT_TRANSCRIPTION_ALLOWED_LANGUAGES)" = '""' ]; then
  echo "  OK: no default transcription language unless one is set"
else echo "  FAIL: default transcription language rendered without being set"; fail=1; fi
lang_r="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set meetingApi.transcriptionLanguage=de --set 'meetingApi.transcriptionAllowedLanguages={de,en}')"
if [ "$(lang_env "$lang_r" DEFAULT_TRANSCRIPTION_LANGUAGE)" = '"de"' ] \
   && [ "$(lang_env "$lang_r" DEFAULT_TRANSCRIPTION_ALLOWED_LANGUAGES)" = '"de,en"' ]; then
  echo "  OK: meetingApi.transcriptionLanguage/AllowedLanguages render as de and de,en"
else echo "  FAIL: transcription language env: $(lang_env "$lang_r" DEFAULT_TRANSCRIPTION_LANGUAGE) $(lang_env "$lang_r" DEFAULT_TRANSCRIPTION_ALLOWED_LANGUAGES)"; fail=1; fi

# ── Generic OIDC sign-in (ADFS / Keycloak) — terminal.oidc + terminal.signinMethods ────────────────
# Off by default: no VEXA_OIDC_* and no CA volume. On: issuer + client id as values, the client secret
# ONLY through the operator's existing Secret, the CA bundle mounted from a ConfigMap or a Secret, and
# a half-filled block refuses to render.
TERM_DEFAULT="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-terminal.yaml)"
if grep -q 'VEXA_OIDC_' <<< "$TERM_DEFAULT" || has 'oidc-ca' <<< "$TERM_DEFAULT" || has 'VEXA_SIGNIN_METHODS' <<< "$TERM_DEFAULT"; then
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

# ── THE CHAT AGENT'S TOOLBELT ON HELM (docs/docs/deployment/helm-agent-tools.mdx) ──────────────────
# The chat agent sends the bot, reads the transcript and stops the bot through the vexa MCP tools. On
# Helm that needs four things together, and a render missing any one of them gives an agent with no
# meeting tools and nothing anywhere saying why: the MCP Deployment + Service, the gateway's /mcp relay
# to it, agent-api's toolbelt pair (VEXA_MCP_URL + VEXA_MCP_DELEGATION_SECRET) with identity
# (admin-api) verifying the same secret, and a worker egress that reaches the gateway — and only the
# gateway: the MCP takes the worker's delegated token through the edge, never directly.
doc() {  # doc <kind> <name> <render> → that one rendered document
  awk -v k="kind: $1" -v n="  name: $2" '
    /^---/ { if (hit) { printf "%s", buf; out=1; exit } buf=""; kk=0; nn=0; next }
    { buf = buf $0 "\n"; if ($0 == k) kk=1; if ($0 == n) nn=1; if (kk && nn) hit=1 }
    END { if (hit && !out) printf "%s", buf }' <<< "$3"
}
tb_ok=1
tb() { if eval "$2"; then echo "  OK: $1"; else echo "  FAIL: $1"; tb_ok=0; fail=1; fi; }
mcp_dep="$(doc Deployment vexa-vexa-mcp "$RENDER")"
mcp_svc="$(doc Service vexa-vexa-mcp "$RENDER")"
gw_dep="$(doc Deployment vexa-vexa-gateway "$RENDER")"
aa_dep="$(doc Deployment vexa-vexa-agent-api "$RENDER")"
ad_dep="$(doc Deployment vexa-vexa-admin-api "$RENDER")"
rt_dep="$(doc Deployment vexa-vexa-runtime "$RENDER")"
w_np="$(doc NetworkPolicy vexa-vexa-worker-egress "$RENDER")"
m_np="$(doc NetworkPolicy vexa-vexa-mcp-ingress "$RENDER")"
sec="$(doc Secret vexa-vexa-secrets "$RENDER")"
tb "the MCP Deployment is rendered by default" '[ -n "$mcp_dep" ] && grep -q "containerPort: 8010" <<< "$mcp_dep"'
tb "the MCP Service fronts it on 8010" 'grep -q "port: 8010" <<< "$mcp_svc" && grep -q "app.kubernetes.io/component: mcp" <<< "$mcp_svc"'
tb "the gateway relays /mcp to the MCP Service" '[ "$(envval MCP_URL "$gw_dep")" = "\"http://vexa-vexa-mcp:8010\"" ]'
tb "agent-api points the worker toolbelt at the gateway /mcp" '[ "$(envval VEXA_MCP_URL "$aa_dep")" = "\"http://vexa-vexa-gateway:8000/mcp\"" ]'
tb "agent-api signs with VEXA_MCP_DELEGATION_SECRET from the chart Secret" 'grep -A4 "name: VEXA_MCP_DELEGATION_SECRET" <<< "$aa_dep" | has "key: VEXA_MCP_DELEGATION_SECRET"'
tb "identity (admin-api) verifies with the same secret" 'grep -A4 "name: VEXA_MCP_DELEGATION_SECRET" <<< "$ad_dep" | has "name: vexa-vexa-secrets"'
tb "the chart Secret carries a generated delegation secret" 'grep -qE "^  VEXA_MCP_DELEGATION_SECRET: \"[A-Za-z0-9]{32,}\"" <<< "$sec"'
tb "agent workers may reach the gateway" 'grep -B1 -A6 "component: gateway" <<< "$w_np" | has "port: 8000"'
tb "agent workers have no direct path to the MCP (8010)" '! grep -qE "component: mcp|port: 8010" <<< "$w_np"'
tb "the MCP admits only the release's own Pods, never a spawned workload" '! grep -q "runtime.managed" <<< "$m_np"'
tb "the MCP runs unprivileged by default (uid 10001, no escalation, caps dropped)" 'grep -q "runAsUser: 10001" <<< "$mcp_dep" && grep -q "allowPrivilegeEscalation: false" <<< "$mcp_dep"'
ocp="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set global.securityContext.deliver=false)"
tb "OpenShift (deliver=false): the MCP Pod names no uid, so restricted-v2 assigns one" '! grep -qE "securityContext|runAsUser" <<< "$(doc Deployment vexa-vexa-mcp "$ocp")"'
nomcp="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set mcp.enabled=false)"
tb "mcp.enabled=false: no MCP and no toolbelt URL (agent-api then says it has no Vexa tools)" '[ -z "$(doc Deployment vexa-vexa-mcp "$nomcp")" ] && ! grep -q "name: VEXA_MCP_URL" <<< "$(doc Deployment vexa-vexa-agent-api "$nomcp")"'
# The harness route (models.runner / llmBaseUrl / llmExtraBody, secrets.llmApiKey): absent by default…
tb "default: no harness route is set (claude-code, ANTHROPIC_*)" '! grep -qE "name: VEXA_RUNNER$|name: VEXA_LLM_BASE_URL$" <<< "$aa_dep$rt_dep"'
route="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set models.runner=openai-agent \
  --set models.llmBaseUrl=http://litellm.ai.svc:4000/v1 --set-string 'models.llmExtraBody={"temperature":0}' \
  --set secrets.llmApiKey=sk-render-test-only)"
# agent-api alone holds the route: the runtime forwards no model key and no route (H5/W1)
tb "the runtime carries no model route and no model key" '! grep -qE "name: (VEXA_RUNNER|VEXA_LLM_BASE_URL|VEXA_LLM_API_KEY|ANTHROPIC_AUTH_TOKEN)$" <<< "$(doc Deployment vexa-vexa-runtime "$route")"'
for d in agent-api; do
  dd="$(doc Deployment "vexa-vexa-$d" "$route")"
  tb "$d carries VEXA_RUNNER=openai-agent" '[ "$(envval VEXA_RUNNER "$dd")" = "\"openai-agent\"" ]'
  tb "$d carries VEXA_LLM_BASE_URL" '[ "$(envval VEXA_LLM_BASE_URL "$dd")" = "\"http://litellm.ai.svc:4000/v1\"" ]'
  tb "$d carries VEXA_LLM_EXTRA_BODY" 'grep -A1 "name: VEXA_LLM_EXTRA_BODY" <<< "$dd" | has temperature'
  tb "$d reads VEXA_LLM_API_KEY from the Secret, never inline" 'grep -A4 "name: VEXA_LLM_API_KEY" <<< "$dd" | has "key: VEXA_LLM_API_KEY" && ! grep -q "sk-render-test-only" <<< "$dd"'
done
# H5/W1: the model proxy — workers reach it and nothing else does; agent-api names it and holds its key
mp_np="$(doc NetworkPolicy vexa-vexa-model-proxy-ingress "$RENDER")"
tb "the model proxy admits agent workers only, on 8102" 'grep -q "vexa.role: worker" <<< "$mp_np" && grep -q "port: 8102" <<< "$mp_np" && ! grep -q "component: gateway" <<< "$mp_np"'
tb "agent workers may reach the model proxy" 'grep -B1 -A6 "component: model-proxy" <<< "$w_np" | has "port: 8102"'
tb "agent-api names the model proxy and reads its sealing key from the Secret" '[ "$(envval VEXA_MODEL_PROXY_URL "$aa_dep")" = "\"http://vexa-vexa-model-proxy:8102\"" ] && grep -A4 "name: VEXA_MODEL_PROXY_SECRET" <<< "$aa_dep" | has "key: VEXA_MODEL_PROXY_SECRET"'
# H5/W1: a worker Secret mount names one key, never a model credential or the chart's own Secret
sm_ok="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set-json 'runtime.workerSecretMounts=[{"secret":"codex-auth","file":"auth.json","mountPath":"/tmp/.codex/auth.json"}]' 2>&1)"
tb "a named subscription key renders into RUNTIME_K8S_SECRET_MOUNTS" 'grep -A1 "name: RUNTIME_K8S_SECRET_MOUNTS" <<< "$sm_ok" | has "codex-auth"'
for bad in '{"secret":"codex-auth","mountPath":"/x"}' '{"secret":"vexa-vexa-secrets","file":"auth.json","mountPath":"/x"}' \
           '{"secret":"keys","file":"ANTHROPIC_API_KEY","mountPath":"/x"}' '{"secret":"keys","file":"VEXA_MODEL_PROXY_SECRET","mountPath":"/x"}' \
           '{"secret":"keys","file":"INTERNAL_API_SECRET","mountPath":"/x"}' '{"secret":"keys","file":"VEXA_MODEL_SECRET_OR","mountPath":"/x"}'; do
  sm_bad="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set-json "runtime.workerSecretMounts=[$bad]" 2>&1 || true)"
  tb "runtime.workerSecretMounts refuses $bad" 'grep -q "runtime.workerSecretMounts\[0\]" <<< "$sm_bad"'
done
tb "the chart Secret carries VEXA_LLM_API_KEY when set" 'grep -q "VEXA_LLM_API_KEY: \"sk-render-test-only\"" <<< "$(doc Secret vexa-vexa-secrets "$route")"'
tb "and not when unset" '! grep -q "VEXA_LLM_API_KEY" <<< "$sec"'
# A mirrored install (Harbor / GitLab registry) pinning one tag spawns its chat workers FROM THE MIRROR.
# This was a literal Docker Hub agent-worker:<tag> reference: offline, no chat worker ever started.
for img in harbor.example/vexa/v012-agent-worker:v012 registry.example:5000/vexa/agent-worker@sha256:0123abcd; do
  mir="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set global.imageTag=0.13.3 --set "runtime.agentWorkerImage=$img")"
  want="\"$(sed -E 's#(@sha256:[a-f0-9]+|:[^:/]+)$##' <<< "$img"):0.13.3\""
  tb "global.imageTag keeps the mirror's worker repository ($img)" '[ "$(envval AGENT_WORKER_IMAGE "$(doc Deployment vexa-vexa-runtime "$mir")")" = "$want" ]'
done
[ "$tb_ok" -eq 1 ] && echo "  OK: the chat agent's toolbelt is wired end to end on Helm"

# ADR-0046 — routines are flows: agent-api reaches flows-api with its operator key whenever flows is on
aa_flows="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
  --show-only templates/deployment-agent-api.yaml)"
aa_noflows="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=false \
  --show-only templates/deployment-agent-api.yaml)"
if grep -q 'name: VEXA_FLOWS_API_URL' <<< "$aa_flows" && grep -q 'flows-api:18200' <<< "$aa_flows" \
   && grep -A4 'name: VEXA_FLOWS_API_KEY' <<< "$aa_flows" | has 'secretKeyRef' \
   && ! grep -q 'VEXA_FLOWS_API' <<< "$aa_noflows"; then
  echo "  OK: agent-api carries VEXA_FLOWS_API_URL and the flows operator key (from its Secret) when flows is on, neither when off"
else echo "  FAIL: agent-api is not wired to flows-api (VEXA_FLOWS_API_URL / VEXA_FLOWS_API_KEY)"; fail=1; fi
# ADR-0048: the session store. Off by default (the chart makes no bucket). With its own scoped key
# named: agent-api logs in as the store's own role and never holds the database admin credential or
# the recordings key; the migrations Job makes the role; the store's bucket is never the recordings
# bucket and its key Secret never the recordings key Secret.
api_default="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-agent-api.yaml)"
if ! grep -q 'VEXA_SESSIONS_' <<< "$api_default" && ! grep -q 'key: POSTGRES_PASSWORD' <<< "$api_default"; then
  echo "  OK: the session store is off by default and agent-api holds no database credential"
else echo "  FAIL: agent-api carries session-store or database credentials with the store off"; fail=1; fi
SS=(--set agentApi.sessions.s3.existingSecret=vexa-sessions-s3)
api_on="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${SS[@]}" --show-only templates/deployment-agent-api.yaml)"
if grep -q 'name: VEXA_SESSIONS_DB_USER' <<< "$api_on" && grep -q 'name: "vexa-sessions-s3"' <<< "$api_on" \
   && grep -q 'value: "vexa-sessions"' <<< "$api_on" \
   && ! grep -qE 'key: (POSTGRES_USER|POSTGRES_PASSWORD)' <<< "$api_on" \
   && ! grep -q 's3-fixture-credentials' <<< "$api_on"; then
  echo "  OK: agent-api reaches the session store as its own role, with its own bucket and key"
else echo "  FAIL: agent-api session-store wiring holds an admin or recordings credential"; fail=1; fi
job_on="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${SS[@]}" --show-only templates/job-agent-sessions.yaml)"
if grep -q 'name: agent-sessions' <<< "$job_on" && grep -q 'control_plane.session_schema' <<< "$job_on"; then
  echo "  OK: the session-schema Job makes the session store's role and tables"
else echo "  FAIL: the session-schema Job does not make the session store's role"; fail=1; fi
rec_bucket="vexa-recordings"   # storage.s3.bucket in values-test.yaml
if [ -n "$rec_bucket" ] && ! helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${SS[@]}" \
     --set agentApi.sessions.s3.bucket="$rec_bucket" >/dev/null 2>&1; then
  echo "  OK: rendering refuses the recordings bucket as the session store's bucket"
else echo "  FAIL: the session store may be pointed at the recordings bucket ($rec_bucket)"; fail=1; fi

# ADR-0047: the optional CRM. Off by default — no crm-api, and no service names its door. On: crm-api
# logs in as its own role (never the admin credential), the crm-schema Job makes the role, and the
# gateway and the MCP name its door.
crm_off="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml")"
if ! grep -q 'crm-api' <<< "$crm_off" && ! grep -q 'name: CRM_API_URL' <<< "$crm_off"; then
  echo "  OK: the CRM is off by default and nothing names its door"
else echo "  FAIL: a default render carries the CRM"; fail=1; fi
CRM=(--set crm.enabled=true --set crm.orgId=acme --set crm.existingSecret=crm-db)
crm_on="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" --show-only templates/crm.yaml)"
if grep -q 'crm.migrate", "init"' <<< "$crm_on" && grep -q 'crm.migrate", "check"' <<< "$crm_on" \
   && grep -q 'postgresql+pg8000://vexa_crm:' <<< "$crm_on" && grep -q 'name: "crm-db"' <<< "$crm_on" \
   && [ "$(grep -c 'key: POSTGRES_PASSWORD' <<< "$crm_on")" -eq 1 ]; then
  echo "  OK: crm-api reaches Postgres as its own role; only the crm-schema Job holds the admin credential"
else echo "  FAIL: the CRM's database wiring is not its own role"; fail=1; fi
gw_on="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" --show-only templates/deployment-gateway.yaml)"
mcp_on="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" --show-only templates/deployment-mcp.yaml)"
if grep -q 'name: CRM_API_URL' <<< "$gw_on" && grep -q 'name: CRM_API_URL' <<< "$mcp_on"; then
  echo "  OK: with the CRM on, the gateway fronts it and the MCP assembles its tools"
else echo "  FAIL: the gateway or the MCP does not name the CRM's door"; fail=1; fi
if ! helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set crm.enabled=true >/dev/null 2>&1; then
  echo "  OK: rendering refuses a CRM with no organisation"
else echo "  FAIL: the CRM renders without crm.orgId"; fail=1; fi
crm_np="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" --set networkPolicy.enabled=true --show-only templates/networkpolicy-namespace.yaml)"
if grep -q 'values: \[admin-api, meeting-api, agent-api, pgbouncer, migrations, crm-api\]' <<< "$crm_np" \
   && grep -q 'name: vexa-vexa-crm-api-ingress' <<< "$crm_np"; then
  echo "  OK: with the CRM on, crm-api reaches Postgres and the chart's pods reach crm-api"
else echo "  FAIL: the network policy does not admit the CRM"; fail=1; fi
# The CRM's outbox (car 19): with flows on it is a Deployment of its own that logs in as the CRM's
# role and holds flows' door, the CRM's publish key and the CRM's own fact seal key (never flows'
# operator key); crm-api holds none of them, and flows-api reads the publish key from the same Secret
# key and the seal's public half from a ConfigMap. With flows off there is no outbox.
crm_fl="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" --set flows.enabled=true)"
outbox="$(doc Deployment vexa-vexa-crm-outbox "$crm_fl")"
crmapi="$(doc Deployment vexa-vexa-crm-api "$crm_fl")"
flowsapi="$(doc Deployment vexa-vexa-flows-api "$crm_fl")"
tb "crm outbox runs crm.outbox --watch as the CRM's role" 'grep -q "crm.outbox\", \"--watch\"" <<< "$outbox" && grep -q "postgresql+pg8000://vexa_crm:" <<< "$outbox" && ! grep -q "key: POSTGRES_PASSWORD" <<< "$outbox"'
tb "crm outbox: FLOWS_API_URL names the flows-api Service" 'grep -A1 "name: FLOWS_API_URL$" <<< "$outbox" | has "http://vexa-vexa-flows-api:18200"'
tb "crm outbox: FLOWS_PUBLISH_KEY is the CRM's publish key from the shared Secret, never flows' operator key" 'grep -A4 "name: FLOWS_PUBLISH_KEY$" <<< "$outbox" | has "name: \"vexa-vexa-secrets\"" && grep -A4 "name: FLOWS_PUBLISH_KEY$" <<< "$outbox" | has "key: VEXA_FLOWS_CRM_PUBLISH_KEY" && ! grep -q "VEXA_FLOWS_API_KEY" <<< "$outbox"'
tb "flows-api reads the same publish key (optional ref)" 'grep -A5 "name: VEXA_FLOWS_CRM_PUBLISH_KEY$" <<< "$flowsapi" | has "key: VEXA_FLOWS_CRM_PUBLISH_KEY" && grep -A5 "name: VEXA_FLOWS_CRM_PUBLISH_KEY$" <<< "$flowsapi" | has "optional: true"'
tb "the chart's Secret generates VEXA_FLOWS_CRM_PUBLISH_KEY" 'grep -qE "^  VEXA_FLOWS_CRM_PUBLISH_KEY: \"[A-Za-z0-9]{64}\"$" <<< "$crm_fl"'
short_pub="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set secrets.crmPublishKey=short 2>&1 || true)"
tb "secrets.crmPublishKey shorter than 32 bytes is refused" 'grep -q "secrets.crmPublishKey must be 32+ bytes" <<< "$short_pub"'
# event.v2 fact seal (car 44): the CRM signs with its OWN Ed25519 key, a Secret only crm-outbox mounts;
# flows-api holds the public half alone, in a ConfigMap. No shared seal secret exists in any render.
fseal="$(doc Secret vexa-vexa-fact-seal-crm-key "$crm_fl")"
fverify="$(doc ConfigMap vexa-vexa-fact-verify-keys "$crm_fl")"
crm_pem() { awk '/^  crm.pem: \|/{f=1;next} f&&/^    /{sub(/^    /,"");print;next} f{exit}' <<< "$1"; }  # crm_pem <configmap doc>
fact_pair_ok() {  # fact_pair_ok <secret doc> <configmap doc>: the ConfigMap's crm.pem IS the key's public half
  local priv pub derived tmp
  tmp="$(mktemp -d)"
  priv="$(awk '/^  signing-key.pem: /{print $2}' <<< "$1")"
  pub="$(crm_pem "$2")"
  [ -n "$priv" ] && [ -n "$pub" ] || { rm -rf "$tmp"; return 1; }
  printf '%s' "$priv" | base64 -d > "$tmp/k" 2>/dev/null || printf '%s' "$priv" | base64 -D > "$tmp/k"
  openssl pkey -in "$tmp/k" -noout -text 2>/dev/null | has 'ED25519 Private-Key' || { rm -rf "$tmp"; return 1; }
  derived="$(openssl pkey -in "$tmp/k" -pubout 2>/dev/null)"
  rm -rf "$tmp"
  [ "$derived" = "$pub" ]
}
if command -v openssl >/dev/null 2>&1; then
  tb "fact seal: an Ed25519 signing-key.pem whose public half is the verify ConfigMap's crm.pem" 'fact_pair_ok "$fseal" "$fverify"'
else
  echo "  SKIP: openssl not installed — fact seal key pair not cross-checked"
fi
tb "crm outbox reads VEXA_FACT_SEAL_KEY_FILE from the fact-seal Secret, and is the only workload that mounts it" 'grep -A1 "name: VEXA_FACT_SEAL_KEY_FILE$" <<< "$outbox" | has "value: /run/vexa-fact-seal/key.pem" && grep -q "secretName: vexa-vexa-fact-seal-crm-key" <<< "$outbox" && [ "$(grep -c "secretName: vexa-vexa-fact-seal-crm-key" <<< "$crm_fl")" -eq 1 ]'
tb "flows-api verifies from the public keys (optional ConfigMap) and mounts no fact-seal Secret" 'grep -A1 "name: VEXA_FACT_VERIFY_KEYS_DIR$" <<< "$flowsapi" | has "value: /run/vexa-fact-verify" && grep -A2 "name: vexa-vexa-fact-verify-keys$" <<< "$flowsapi" | has "optional: true" && ! grep -q "fact-seal-crm-key" <<< "$flowsapi"'
tb "crm outbox holds no internal secret and crm-api no flows key or seal" '! grep -q "INTERNAL_API_SECRET" <<< "$outbox" && ! grep -qE "name: (FLOWS_PUBLISH_KEY|VEXA_FACT_SEAL_KEY_FILE)$" <<< "$crmapi" && ! grep -q "fact-seal-crm-key" <<< "$crmapi"'
crm_pre="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" --set flows.enabled=true --set secrets.existingSecretName=shared-secrets)"
tb "no render carries a shared VEXA_FACT_SEAL_SECRET (chart Secret or secrets.existingSecretName)" '! grep -q "VEXA_FACT_SEAL_SECRET" <<< "$crm_fl" && ! grep -q "VEXA_FACT_SEAL_SECRET" <<< "$crm_pre" && ! grep -q "VEXA_FACT_SEAL_SECRET" <<< "$RENDER"'
if command -v openssl >/dev/null 2>&1; then
  NOT_ED="$(openssl genpkey -algorithm rsa -pkeyopt rsa_keygen_bits:2048 2>/dev/null)"
  rsa_seal="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" --set flows.enabled=true --set-string factSeal.crm.signingKey="$NOT_ED" 2>&1 || true)"
  tb "factSeal.crm.signingKey that is not Ed25519 (RSA) refuses the render, naming the fix" 'grep -q "factSeal.crm.signingKey must be an Ed25519 private key" <<< "$rsa_seal" && grep -q "openssl genpkey -algorithm ed25519" <<< "$rsa_seal"'
  OWN_PUB="$(openssl genpkey -algorithm ed25519 2>/dev/null | openssl pkey -pubout 2>/dev/null)"
  no_pub="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" --set flows.enabled=true --set factSeal.crm.existingSecret=my-seal 2>&1 || true)"
  ext_seal="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" --set flows.enabled=true --set factSeal.crm.existingSecret=my-seal --set-string factSeal.crm.publicKey="$OWN_PUB")"
  tb "factSeal.crm.existingSecret: refused without publicKey; with it no generated Secret, the ConfigMap carries the given key, crm-outbox mounts the named Secret" 'grep -q "factSeal.crm.publicKey is required with factSeal.crm.existingSecret" <<< "$no_pub" && [ -z "$(doc Secret vexa-vexa-fact-seal-crm-key "$ext_seal")" ] && [ "$(crm_pem "$(doc ConfigMap vexa-vexa-fact-verify-keys "$ext_seal")")" = "$OWN_PUB" ] && grep -q "secretName: my-seal" <<< "$(doc Deployment vexa-vexa-crm-outbox "$ext_seal")"'
  unset NOT_ED OWN_PUB
fi
crm_only="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}")"
tb "with crm off (or flows off) neither fact-seal object renders" '! grep -qE "vexa-vexa-fact-(seal-crm-key|verify-keys)" <<< "$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --show-only templates/fact-seal-keys.yaml 2>&1 || true)" && ! grep -qE "^  name: vexa-vexa-fact-(seal-crm-key|verify-keys)$" <<< "$crm_only"'
tb "with flows off there is no crm outbox" '! grep -q "vexa-vexa-crm-outbox" <<< "$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}")"'
crm_np_fl="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" --set flows.enabled=true --set networkPolicy.enabled=true --show-only templates/networkpolicy-namespace.yaml)"
tb "with the CRM and flows on, the crm outbox reaches Postgres" 'grep -q "values: \[admin-api, meeting-api, agent-api, pgbouncer, migrations, crm-api, crm-outbox\]" <<< "$crm_np_fl"'

# ── THE SESSION STORE'S SCHEMA JOB RUNS ON A FIRST INSTALL ──────────────────────────────────────────
# Helm takes every manifest carrying a helm.sh/hook annotation out of the release and runs it only on
# the events it names. Annotated `pre-upgrade` alone, the agent-sessions-schema Job was never created
# by `helm install` (measured on a fresh OpenShift-shaped install of 0.13.3-alpha.1: agent-api held in
# its session-schema initContainer, the MCP crash-looping, until a second `helm upgrade`). A
# `post-install` hook would deadlock `helm install --wait` and an Argo CD PostSync instead. So it is a
# release resource whose name carries a hash of its own spec: created on install, a new Job when its
# image changes (never a patch of an immutable one), the same name when nothing changed.
ss_job() { helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set agentApi.sessions.s3.existingSecret=sessions-s3 "$@" --show-only templates/job-agent-sessions.yaml; }
j1="$(ss_job)"; j1b="$(ss_job)"; j2="$(ss_job --set agentApi.image.tag=other-tag)"
n1="$(awk '/^kind: Job$/{k=1} k&&/^  name:/{print $2; exit}' <<< "$j1")"
n1b="$(awk '/^kind: Job$/{k=1} k&&/^  name:/{print $2; exit}' <<< "$j1b")"
n2="$(awk '/^kind: Job$/{k=1} k&&/^  name:/{print $2; exit}' <<< "$j2")"
tb "the session-schema Job is a release resource (no helm.sh/hook), so helm install creates it" '[ -n "$n1" ] && ! grep -q "helm.sh/hook" <<< "$j1"'
tb "its name is stable when nothing changed" '[ "$n1" = "$n1b" ]'
tb "its name changes with the image, so an upgrade never patches an immutable Job" '[ "$n1" != "$n2" ] && [[ "$n1" == vexa-vexa-agent-sessions-schema-* ]]'

# ── THE CRM'S SCHEMA JOB RUNS ON A FIRST INSTALL (10-K / PROD-READINESS S2) ─────────────────────────
# Same trap as the session store: a `pre-upgrade`-only hook is never created by `helm install`, so
# crm-api waited in its crm-schema-ready initContainer forever. A release resource named by its spec.
crm_job() { helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" "$@" --show-only templates/crm.yaml; }
c1="$(crm_job)"; c1b="$(crm_job)"; c2="$(crm_job --set crm.image.tag=other-tag)"
cjob() { awk '/^kind: Job$/{k=1} k&&/^  name:/{print $2; exit}' <<< "$1"; }
cn1="$(cjob "$c1")"; cn1b="$(cjob "$c1b")"; cn2="$(cjob "$c2")"
tb "the crm-schema Job is a release resource (no hook on it), so helm install creates it" '[[ "$cn1" == vexa-vexa-crm-schema-* ]] && ! grep -q "helm.sh/hook" <<< "$(doc Job "$cn1" "$c1")"'
tb "its name is stable when nothing changed, and changes with the image" '[ "$cn1" = "$cn1b" ] && [ "$cn1" != "$cn2" ]'

# ── EVERY RELEASE IMAGE FALLS BACK TO v<appVersion> (10-J / PROD-READINESS S3) ──────────────────────
# agent-api's session-schema initContainer, the session-schema Job, the workspaces S3 migration Job
# and every crm container read `global.imageTag | default <component>.image.tag` with both empty, so
# a default upgrade rendered `repo:` — InvalidImageName — and agent-api never started.
REL_TAG="v$(awk -F'"' '/^appVersion:/ {print $2}' "$CHART/Chart.yaml")"
img_all="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${CRM[@]}" \
  --set agentApi.sessions.s3.existingSecret=sessions-s3 --set flows.enabled=true 2>&1)"
tb "no rendered image ends in a bare ':' (an empty tag)" '! grep -nE "image: \"?[^\" ]+:\"?$" <<< "$img_all"'
tb "agent-api, its session-schema Job and the crm containers pull the chart's release tag" \
  '[ "$(grep -cE "image: \"vexaai/vexa-agent-api:${REL_TAG//./\\.}\"$" <<< "$img_all")" -ge 3 ] && [ "$(grep -cE "image: \"vexaai/vexa-crm:${REL_TAG//./\\.}\"$" <<< "$img_all")" -ge 3 ]'

# ── 0.12 COMPATIBILITY SWITCHES SHIP CLOSED (10-A, 10-B, 10-F) ──────────────────────────────────────
compat_def="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml")"
gw_c="$(doc Deployment vexa-vexa-gateway "$compat_def")"; ma_c="$(doc Deployment vexa-vexa-meeting-api "$compat_def")"
tb "default: /ws takes the REST scopes on the gateway and meeting-api (strict)" \
  '[ "$(envval VEXA_WS_SCOPE_MODE "$gw_c")" = "\"strict\"" ] && [ "$(envval VEXA_WS_SCOPE_MODE "$ma_c")" = "\"strict\"" ]'
tb "default: stored webhook URLs get the outbound-url.v1 guard (strict)" '[ "$(envval VEXA_WEBHOOK_STORED_URL_POLICY "$ma_c")" = "\"strict\"" ]'
compat_open="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set compat.wsScopeMode=legacy --set compat.webhookStoredUrlPolicy=v012)"
tb "the opt-ins reach both services" \
  '[ "$(envval VEXA_WS_SCOPE_MODE "$(doc Deployment vexa-vexa-gateway "$compat_open")")" = "\"legacy\"" ] && [ "$(envval VEXA_WEBHOOK_STORED_URL_POLICY "$(doc Deployment vexa-vexa-meeting-api "$compat_open")")" = "\"v012\"" ]'
# 10-E: the upgrade hand-off for 0.12 bots' unsigned transcript entries ships off; the runbook opens it.
tb "default: the unsigned-segment hand-off is off (VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S=0)" \
  '[ "$(envval VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S "$ma_c")" = "\"0\"" ]'
tb "compat.legacyUnsignedWindowSeconds opens it for the cut-over" \
  '[ "$(envval VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S "$(doc Deployment vexa-vexa-meeting-api "$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set compat.legacyUnsignedWindowSeconds=14400)")")" = "\"14400\"" ]'
sx_def="$(doc Deployment vexa-vexa-meeting-api "$compat_def")"
sx_set="$(doc Deployment vexa-vexa-meeting-api "$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set credentialBroker.botSigninExtraHosts='adfs.bank.example\,login.bank.okta.com')")"
tb "the sign-in browser's extra hosts are empty by default and reach meeting-api when set" \
  '[ "$(envval VEXA_BOT_SIGNIN_EXTRA_HOSTS "$sx_def")" = "\"\"" ] && [ "$(envval VEXA_BOT_SIGNIN_EXTRA_HOSTS "$sx_set")" = "\"adfs.bank.example,login.bank.okta.com\"" ]'
refuse "a wildcard sign-in host" "wildcards are not accepted" -f "$CHART/values-test.yaml" --set credentialBroker.botSigninExtraHosts='adfs.bank.example\,*.okta.bank.example'

# A3: meeting-api and admin-api tell flows their facts on Helm as they do under compose — the in-chart
# flows-api and its operator key, only when flows is on. Without flows they hold no flows edge at all.
fl_on="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true)"
fl_url="\"http://vexa-vexa-flows-api:18200\""
for svc in meeting-api admin-api; do
  d_on="$(doc Deployment "vexa-vexa-$svc" "$fl_on")"; d_off="$(doc Deployment "vexa-vexa-$svc" "$compat_def")"
  tb "flows on: $svc publishes to the in-chart flows-api" '[ "$(envval VEXA_FLOWS_API_URL "$d_on")" = "$fl_url" ]'
  tb "flows on: $svc presents the flows operator key from the flows Secret" \
    'grep -A4 "name: VEXA_FLOWS_API_KEY$" <<< "$d_on" | has "name: vexa-vexa-flows" && grep -A4 "name: VEXA_FLOWS_API_KEY$" <<< "$d_on" | has "key: VEXA_FLOWS_API_KEY"'
  tb "flows off (negative control): $svc has no flows edge" '! grep -qE "name: VEXA_FLOWS_API_(URL|KEY)$" <<< "$d_off"'
done

# ── Production-shape options (prod-shape-0133): a hosted install runs this chart unpatched ──
echo "=== production-shape options ==="
PS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
  --set 'global.tolerations[0].key=vexa.ai/pool' --set 'global.tolerations[0].operator=Exists' \
  --set 'global.nodeSelector.vexa\.ai/pool=main' --set 'global.imagePullSecrets[0].name=pull' \
  --set 'flows.extraEnv[0].name=PS_FLOWS_EXTRA' --set 'flows.extraEnv[0].value=1' \
  --set 'gateway.deploymentAnnotations.argocd\.argoproj\.io/sync-wave=5' \
  --set runtime.backend=k8s --set runtime.botSandbox.installer.namespace=vexa-node-bootstrap)"
PSJ="$(python3 -c '
import sys, yaml, json
docs=[d for d in yaml.safe_load_all(sys.stdin) if d]
out={}
for d in docs:
  n=d["metadata"]["name"]; sp=d.get("spec",{}).get("template",{}).get("spec",{})
  out[n]={"kind":d["kind"],"ns":d["metadata"].get("namespace",""),"ann":d["metadata"].get("annotations") or {},
   "strategy":d.get("spec",{}).get("strategy"),"tol":bool(sp.get("tolerations")),"ns_sel":bool(sp.get("nodeSelector")),
   "pull":[x["name"] for x in sp.get("imagePullSecrets",[])],
   "env":[e["name"] for c in sp.get("containers",[]) for e in c.get("env",[])]}
print(json.dumps(out))' <<< "$PS")"
q() { python3 -c "import json,sys; o=json.loads(sys.argv[1]); print($2)" "$PSJ"; }
for c in flows-worker flows-api; do
  tb "$c takes global tolerations, nodeSelector and pull secrets" "[ \"\$(q x \"o['vexa-vexa-$c']['tol'] and o['vexa-vexa-$c']['ns_sel'] and o['vexa-vexa-$c']['pull']==['pull']\")\" = True ]"
  tb "$c rolls surge-first (maxUnavailable 0)" "[ \"\$(q x \"o['vexa-vexa-$c']['strategy']['rollingUpdate']['maxUnavailable']\")\" = 0 ]"
  tb "$c carries flows.extraEnv" "[ \"\$(q x \"'PS_FLOWS_EXTRA' in o['vexa-vexa-$c']['env']\")\" = True ]"
done
tb "gateway Deployment carries gateway.deploymentAnnotations" "[ \"\$(q x \"o['vexa-vexa-gateway']['ann'].get('argocd.argoproj.io/sync-wave')\")\" = 5 ]"
tb "the seccomp installer renders into installer.namespace" "[ \"\$(q x \"o['vexa-vexa-bot-seccomp']['ns']\")\" = vexa-node-bootstrap ]"
DEF="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-gateway.yaml)"
tb "no annotations block on the gateway Deployment by default" '! awk "/^metadata:/{m=1} /^spec:/{m=0} m" <<< "$DEF" | has "^  annotations:"'

BS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set meetingApi.botS3ExistingSecret=bot-s3 --show-only templates/deployment-meeting-api.yaml)"
tb "meetingApi.botS3ExistingSecret reads the bots' S3 pair from a Secret" 'grep -A3 "name: BOT_S3_ACCESS_KEY" <<< "$BS" | has "name: \"bot-s3\""'

# PRD 0.13.4 S2: the relay password by reference, and the recipient allowlist, reach every flows pod
REF="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
  --set flows.mail.address=bot@example.org --set flows.mail.smtp.passwordRef=broker:mail/smtp \
  --set flows.mail.recipientAllow=dmitry@vexa.ai --show-only templates/flows.yaml)"
tb "flows.mail.smtp.passwordRef renders the ref, the broker URL and the sensors key on every flows pod" \
  '[ "$(grep -c "name: VEXA_MAIL_SMTP_PASSWORD_REF" <<< "$REF")" = "$(grep -c "name: VEXA_MAIL_SMTP_HOST" <<< "$REF")" ] && grep -q "path: sensors/key" <<< "$REF" && grep -A1 "name: VEXA_FLOWS_SENSORS_BROKER_URL" <<< "$REF" | has credential-broker'
tb "flows.mail.recipientAllow reaches the flows pods" 'grep -A1 "name: VEXA_MAIL_RECIPIENT_ALLOW" <<< "$REF" | has "dmitry@vexa.ai"'
tb "a malformed flows.mail.smtp.passwordRef is refused" '! helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --set flows.mail.smtp.passwordRef=smtp >/dev/null 2>&1'
# WS-S3: agentApi.workspaces.backend=s3 — the bucket is the truth, pods keep an emptyDir cache.
WS=(--set agentApi.workspaces.backend=s3 --set agentApi.workspaces.s3.bucket=vexa-workspaces
    --set agentApi.workspaces.s3.endpoint=https://s3.corp.example --set agentApi.workspaces.s3.existingSecret=ws-key
    --set agentApi.workspaces.s3.caBundle.configMapName=corp-ca --set agentApi.workspaces.workerCacheSize=2Gi)
WSA="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${WS[@]}" --show-only templates/deployment-agent-api.yaml)"
WSR="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${WS[@]}" --show-only templates/deployment-runtime.yaml)"
WSV="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-agent-api.yaml)"
tb "backend volume (default) mounts the claim and sets VEXA_WORKSPACE_BACKEND=volume" 'grep -q "claimName: vexa-vexa-agent-workspaces" <<< "$WSV" && grep -A1 "name: VEXA_WORKSPACE_BACKEND" <<< "$WSV" | has "\"volume\""'
tb "backend s3: agent-api caches on an emptyDir, not the claim" 'grep -A1 "name: workspaces$" <<< "$WSA" | has "emptyDir" && ! grep -q "claimName: vexa-vexa-agent-workspaces" <<< "$WSA"'
tb "backend s3: the scoped key comes from the named Secret" 'grep -A3 "name: VEXA_WORKSPACE_S3_SECRET_KEY" <<< "$WSA" | has "name: \"ws-key\""'
tb "backend s3: a private CA bundle is mounted and named" 'grep -q "VEXA_WORKSPACE_S3_CA_BUNDLE" <<< "$WSA" && grep -q "name: \"corp-ca\"" <<< "$WSA"'
tb "backend s3: workers get a private emptyDir from the runtime" 'grep -A1 "name: VEXA_WORKSPACE_MOUNT_SOURCE" <<< "$WSR" | has "emptyDir:" && grep -A1 "RUNTIME_WORKSPACE_CACHE_SIZE" <<< "$WSR" | has 2Gi'
tb "backend s3 without a bucket refuses to render" '! helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set agentApi.workspaces.backend=s3 >/dev/null 2>&1'
tb "backend s3 refuses the recordings key" '! helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${WS[@]}" --set agentApi.workspaces.s3.existingSecret=s3-fixture-credentials >/dev/null 2>&1'
tb "the migration Job mounts the old claim read-only" 'helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${WS[@]}" --set agentApi.workspaces.s3.migrate.enabled=true --show-only templates/job-workspaces-s3-migrate.yaml | grep -B2 -A2 "claimName: vexa-vexa-agent-workspaces" | has "readOnly: true"'
# OpenShift / multi-node profile (values-openshift.yaml). Measured on a three-node cluster with
# network-attached block storage and restricted Pod Security: with the defaults, agent workers
# scheduled off agent-api's node wait for the ReadWriteOnce workspace volume and the chat never
# answers, and the hostPath seccomp installer is refused by Pod Security. The profile carries the
# values that install there; the RWX class is the operator's to name.
OS_PROFILE="$CHART/values-openshift.yaml"
tb "the chart ships an OpenShift / multi-node profile" '[ -f "$OS_PROFILE" ]'
if [ -f "$OS_PROFILE" ]; then
  R_OS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" -f "$OS_PROFILE" --set agentApi.workspaces.storageClassName=rwx-class)"
  OS_PVC="$(doc PersistentVolumeClaim vexa-vexa-agent-workspaces "$R_OS")"
  tb "profile: no hostPath seccomp installer DaemonSet" '! grep -q "component: bot-seccomp" <<< "$R_OS"'
  tb "profile: bot Pods still name the Localhost profile" '[ "$(envval RUNTIME_K8S_BOT_SECCOMP_PROFILE "$R_OS")" = "\"vexa/seccomp-userns.json\"" ]'
  tb "profile: the workspace store is ReadWriteMany on the named class" 'grep -q "ReadWriteMany" <<< "$OS_PVC" && grep -q "storageClassName: \"rwx-class\"" <<< "$OS_PVC"'
  tb "profile: agent workers keep no added capability" '[ "$(envval RUNTIME_K8S_AGENT_WORKER_CAPABILITIES "$R_OS")" = "\"[]\"" ]'
  tb "profile: the chart delivers no securityContext (restricted-v2 injects it)" '! grep -q "runAsUser: 10001" <<< "$R_OS"'
fi

# F1b: the terminal's page policy admits the LiveKit server a browser joins, from the same value agent-api hands it.
TLK="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set livekit.enabled=true --set livekit.url=wss://calls.example.test --show-only templates/deployment-terminal.yaml)"
TNO="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-terminal.yaml)"
tb "livekit.enabled hands the terminal LIVEKIT_URL for its page policy" 'grep -A1 "name: LIVEKIT_URL" <<< "$TLK" | has "\"wss://calls.example.test\""'
tb "calls off: the terminal's LIVEKIT_URL is empty" 'grep -A1 "name: LIVEKIT_URL" <<< "$TNO" | has "value: \"\""'

# In-chart databases: a startup probe holds liveness off through a slow first start (postgres initdb,
# redis AOF replay), so a loaded node does not kill the server mid-initialisation; no probe on them
# times out in under 5 s.
PGS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/statefulset-postgres.yaml)"
RDS="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-redis.yaml)"
startup_budget() { awk '/startupProbe:/{f=1} f&&/periodSeconds:/{p=$2} f&&/failureThreshold:/{print p*$2; exit}' <<< "$1"; }
min_timeout() { grep -E '^[[:space:]]+timeoutSeconds:' <<< "$1" | awk '{print $2}' | sort -n | sed -n 1p; }
tb "postgres has a pg_isready startup probe" 'grep -A3 "startupProbe:" <<< "$PGS" | has pg_isready'
tb "postgres startup budget covers initdb on a loaded node (>= 300 s)" '[ "$(startup_budget "$PGS")" -ge 300 ]'
tb "postgres liveness has no initialDelay race and a >= 5 s timeout" '! grep -q initialDelaySeconds <<< "$PGS" && [ "$(grep -c timeoutSeconds <<< "$PGS")" -eq 3 ] && [ "$(min_timeout "$PGS")" -ge 5 ]'
tb "redis has a startup probe with a >= 300 s budget" 'grep -q "startupProbe:" <<< "$RDS" && [ "$(startup_budget "$RDS")" -ge 300 ]'
tb "redis probes time out in >= 5 s" '[ "$(grep -c timeoutSeconds <<< "$RDS")" -eq 3 ] && [ "$(min_timeout "$RDS")" -ge 5 ]'
tb "postgres.probes is operator-tunable" '[ "$(startup_budget "$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set postgres.probes.startup.failureThreshold=120 --show-only templates/statefulset-postgres.yaml)")" -eq 1200 ]'

# Corporate TLS inspection (global.extraCA). Unset renders nothing new; set, every Vexa Deployment and
# Job writes image bundle + operator PEM into an emptyDir before the app starts and points every
# runtime's trust env at it, and the runtime hands the same to the Pods it spawns.
XCA_FULL=(--set flows.enabled=true,crm.enabled=true,crm.orgId=x,dashboard.enabled=true,flows.mail.enabled=true,flows.mail.address=a@b.c,flows.databaseName=flows,migrations.enabled=true,signals.enabled=true)
xca_check() { python3 -c '
import sys, json, yaml
mode = sys.argv[1]; docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
infra = {"redis", "postgres", "pgbouncer", "livekit", "install"}
seen, names = 0, set()
for d in docs:
    if d["kind"] not in ("Deployment", "Job", "StatefulSet"): continue
    sp = d["spec"]["template"]["spec"]
    for c in sp["containers"]:
        if c["name"] in infra: continue
        seen += 1
        names.add(c["name"])
        env = {e["name"]: e.get("value") for e in c.get("env", [])}
        inits = sp.get("initContainers", [])
        if mode == "off":
            assert not any(i["name"] == "vexa-ca-bundle" for i in inits), d["metadata"]["name"]
            assert "SSL_CERT_FILE" not in env and "vexa-extra-ca" not in str(sp.get("volumes")), d["metadata"]["name"]
            continue
        n = d["metadata"]["name"]
        assert inits and inits[0]["name"] == "vexa-ca-bundle", n
        vols = {v["name"]: v for v in sp["volumes"]}
        assert "emptyDir" in vols["vexa-ca-bundle"], n
        src = vols["vexa-extra-ca"].get("configMap") or vols["vexa-extra-ca"].get("secret")
        assert src["items"] == [{"key": "root.pem", "path": "ca.crt"}], n
        m = {x["name"]: x for x in c["volumeMounts"]}
        assert m["vexa-ca-bundle"]["readOnly"] and m["vexa-extra-ca"]["readOnly"], n
        for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "GIT_SSL_CAINFO"):
            assert env[k] == "/etc/vexa/ca-bundle/ca-bundle.crt", (n, k)
        assert env["NODE_EXTRA_CA_CERTS"] == "/etc/vexa/extra-ca/ca.crt", n
        assert ("AWS_CA_BUNDLE" in env) == (mode != "s3ca"), n
        sc = inits[0].get("securityContext")
        if mode == "osh":
            assert sc is None, n                      # restricted-v2 injects it
        else:
            assert sc["allowPrivilegeEscalation"] is False and "runAsUser" not in sc, n
        assert inits[0]["resources"]["limits"], n
        if c["name"] == "runtime":
            j = json.loads(env["RUNTIME_K8S_EXTRA_CA"])
            assert j["key"] == "root.pem" and j["awsCaBundle"] == (mode != "s3ca"), j
assert seen >= 10, seen
print("ok", seen, " ".join(sorted(names)))
' "$1"; }
X_OFF="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${XCA_FULL[@]}")"
tb "extraCA unset: no bundle writer, no CA volume, no trust env anywhere" 'xca_check off <<< "$X_OFF" >/dev/null'
X_ON="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${XCA_FULL[@]}" --set global.extraCA.configMap=corp-ca,global.extraCA.key=root.pem)"
tb "extraCA (configMap): every Vexa Deployment/Job bundles it and trusts it" 'xca_check on <<< "$X_ON" >/dev/null'
X_SEC="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${XCA_FULL[@]}" --set global.extraCA.secret=corp-ca,global.extraCA.key=root.pem)"
tb "extraCA (secret): the same from a Secret" 'xca_check on <<< "$X_SEC" >/dev/null && grep -q "secretName: \"corp-ca\"" <<< "$X_SEC"'
X_S3="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${XCA_FULL[@]}" --set global.extraCA.configMap=corp-ca,global.extraCA.key=root.pem,storage.s3.caBundle.configMapName=s3-ca)"
tb "extraCA leaves AWS_CA_BUNDLE to storage.s3.caBundle when that is set" 'xca_check s3ca <<< "$X_S3" >/dev/null'
X_OSH="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" -f "$CHART/values-openshift.yaml" "${XCA_FULL[@]}" --set agentApi.workspaces.storageClassName=rwx --set global.extraCA.configMap=corp-ca,global.extraCA.key=root.pem)"
tb "extraCA under the OpenShift profile: the bundle writer brings no securityContext of its own" 'xca_check osh <<< "$X_OSH" >/dev/null'
tb "extraCA reaches the model proxy and signals (both call a model through the inspection)" 'xca_check on <<< "$X_ON" | has "model-proxy" && xca_check on <<< "$X_ON" | has "signals"'
X_MIG="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" "${WS[@]}" --set agentApi.workspaces.s3.migrate.enabled=true --set global.extraCA.secret=corp-ca,global.extraCA.key=root.pem --show-only templates/job-workspaces-s3-migrate.yaml)"
tb "extraCA reaches the workspace-store migration Job beside its own S3 CA" 'grep -q "name: vexa-ca-bundle" <<< "$X_MIG" && grep -q "name: workspace-s3-ca" <<< "$X_MIG" && grep -A1 "name: SSL_CERT_FILE" <<< "$X_MIG" | has ca-bundle.crt'
X_ADM="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-admin-api.yaml \
  --set adminApi.photos.existingSecret=vexa-photos-s3 \
  --set terminal.oidc.issuer=https://adfs.example.test/adfs --set terminal.oidc.clientId=vexa-terminal --set terminal.oidc.existingSecret=vexa-oidc \
  --set terminal.oidc.caBundle.configMap=oidc-ca --set global.extraCA.configMap=corp-ca,global.extraCA.key=root.pem)"
tb "admin-api: extraCA sits beside its photo store and OIDC CA under one volumes key and one volumeMounts key" 'python3 -c "
import sys, yaml
d = yaml.safe_load(sys.stdin); sp = d[\"spec\"][\"template\"][\"spec\"]; c = sp[\"containers\"][0]
vols = [v[\"name\"] for v in sp[\"volumes\"]]; mounts = [m[\"name\"] for m in c[\"volumeMounts\"]]
assert {\"vexa-extra-ca\", \"vexa-ca-bundle\", \"s3-client\"} <= set(vols) and len(vols) == len(set(vols)), vols
assert {\"vexa-extra-ca\", \"vexa-ca-bundle\", \"s3-client\", \"oidc-ca\"} <= set(mounts) and len(mounts) == len(set(mounts)), mounts
assert any(\"oidc\" in v for v in vols), vols
" <<< "$X_ADM" && [ "$(grep -c "^      volumes:" <<< "$X_ADM")" -eq 1 ]'
X_INJ="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" -f "$CHART/values-openshift.yaml" --set agentApi.workspaces.storageClassName=rwx --set global.extraCA.configMap=trusted-ca,global.extraCA.key=ca-bundle.crt --show-only templates/deployment-runtime.yaml)"
tb "extraCA from an OpenShift injected trusted-CA ConfigMap (key ca-bundle.crt) reaches the runtime and its spawned Pods" 'grep -q "key: \"ca-bundle.crt\", path: ca.crt" <<< "$X_INJ" && grep -A1 "name: RUNTIME_K8S_EXTRA_CA" <<< "$X_INJ" | has "ca-bundle.crt"'
tb "extraCA naming both a configMap and a secret is refused" '! helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set global.extraCA.configMap=a,global.extraCA.secret=b >/dev/null 2>&1'

SCR_ON="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-meeting-api.yaml)"
tb "calls.transcribeSharedScreen by default: the shared screen is transcribed" 'grep -A1 "name: CALL_TRANSCRIBE_SHARED_SCREEN$" <<< "$SCR_ON" | has -F "value: \"true\""'
SCR_OFF="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --show-only templates/deployment-meeting-api.yaml --set meetingApi.calls.transcribeSharedScreen=false)"
tb "calls.transcribeSharedScreen=false (a boolean) renders \"false\"" 'grep -A1 "name: CALL_TRANSCRIBE_SHARED_SCREEN$" <<< "$SCR_OFF" | has -F "value: \"false\""'
# The flows tier's one credential is VEXA_FLOWS_SERVICE_KEY, and it opens only the gateway's service
# door: a ClusterIP Service on 8002 that exists only with flows on, never a port on the public gateway
# Service, reachable only from flows-*, signals and the terminal. Flows holds no admin token and no
# internal secret, and agent-api no longer admits flows Pods directly.
DOOR_ON="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true,signals.enabled=true,flows.mail.enabled=true,flows.mail.address=a@b.c,crm.enabled=true,crm.orgId=x)"
DOOR_OFF="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=false,signals.enabled=true)"
door_check() { python3 -c '
import sys, yaml
mode = sys.argv[1]; docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
by = {(d["kind"], d["metadata"]["name"]): d for d in docs}
door = by.get(("Service", "vexa-vexa-gateway-door"))
np = by.get(("NetworkPolicy", "vexa-vexa-gateway-door-ingress"))
gw = by[("Deployment", "vexa-vexa-gateway")]["spec"]["template"]["spec"]["containers"][0]
gw_env = {e["name"]: e for e in gw.get("env", [])}
gw_ports = {p["containerPort"] for p in gw.get("ports", [])}
def comps(n):
    out = set()
    for ing in n["spec"].get("ingress", []):
        for fr in ing.get("from", []):
            ps = fr.get("podSelector", {})
            out |= set(ps.get("matchLabels", {}).get("app.kubernetes.io/component", "").split()) - {""}
            for e in ps.get("matchExpressions", []):
                out |= set(e.get("values", []))
    return out
if mode == "off":
    assert door is None and np is None, "door rendered with flows off"
    assert "VEXA_FLOWS_SERVICE_KEY" not in gw_env and 8002 not in gw_ports, "gateway serves a door with flows off"
    print("ok"); sys.exit()
check = sys.argv[2]
if check == "service":
    assert door["spec"]["type"] == "ClusterIP", door["spec"].get("type")
    assert [p["port"] for p in door["spec"]["ports"]] == [8002], door["spec"]["ports"]
    assert door["spec"]["selector"]["app.kubernetes.io/component"] == "gateway", door["spec"]["selector"]
    pub = by[("Service", "vexa-vexa-gateway")]
    assert 8002 not in [p["port"] for p in pub["spec"]["ports"]], pub["spec"]["ports"]
    assert 8002 in gw_ports and gw_env["VEXA_FLOWS_SERVICE_KEY"]["valueFrom"]["secretKeyRef"]["key"] == "VEXA_FLOWS_SERVICE_KEY"
elif check == "netpol":
    assert comps(np) == {"flows-api", "flows-worker", "flows-mailbox", "signals", "terminal"}, comps(np)
    assert [p["port"] for i in np["spec"]["ingress"] for p in i.get("ports", [])] == [8002], np["spec"]["ingress"]
    aa = by[("NetworkPolicy", "vexa-vexa-agent-api-ingress")]
    assert not any(c.startswith("flows") for c in comps(aa)), comps(aa)
elif check == "flows":
    seen = 0
    for name in ("flows-api", "flows-worker", "flows-mailbox"):
        c = [c for c in by[("Deployment", "vexa-vexa-" + name)]["spec"]["template"]["spec"]["containers"] if c["name"] == name][0]
        env = {e["name"]: e for e in c.get("env", [])}
        assert env["VEXA_FLOWS_SERVICE_KEY"]["valueFrom"]["secretKeyRef"]["key"] == "VEXA_FLOWS_SERVICE_KEY", name
        text = yaml.safe_dump(c)
        for bad in ("ADMIN_API_TOKEN", "INTERNAL_API_SECRET", "VEXA_FLOWS_ADMIN_KEY", "VEXA_INTERNAL_SECRET"):
            assert bad not in text, (name, bad)
        for k, v in (("VEXA_FLOWS_AGENT_API_URL", "/agent"), ("VEXA_FLOWS_ADMIN_API_URL", "/identity"),
                     ("VEXA_FLOWS_GATEWAY_URL", "/meetings"), ("VEXA_FLOWS_CRM_API_URL", "/crm")):
            assert env[k]["value"] == "http://vexa-vexa-gateway-door:8002" + v, (name, k, env[k])
        seen += 1
    assert seen == 3
elif check == "signals":
    sg = {e["name"]: e for e in by[("Deployment", "vexa-vexa-signals")]["spec"]["template"]["spec"]["containers"][0].get("env", [])}
    assert sg["VEXA_FLOWS_API_URL"]["value"] == "http://vexa-vexa-gateway-door:8002/to-flows", sg["VEXA_FLOWS_API_URL"]
print("ok")
' "$@"; }
tb "flows door: gateway-door is a ClusterIP Service on 8002 only, the public gateway Service has no 8002, the gateway serves it with the flows key" 'door_check on service <<< "$DOOR_ON" >/dev/null'
tb "flows door: gateway-door-ingress admits exactly flows-api, flows-worker, flows-mailbox, signals, terminal on 8002; agent-api ingress admits no flows Pod" 'door_check on netpol <<< "$DOOR_ON" >/dev/null'
tb "flows door: every flows container sources VEXA_FLOWS_SERVICE_KEY, reaches agent/identity/meetings/crm through the door, and holds no ADMIN_API_TOKEN or INTERNAL_API_SECRET" 'door_check on flows <<< "$DOOR_ON" >/dev/null'
tb "flows door: signals hands its facts to flows through the door's /to-flows" 'door_check on signals <<< "$DOOR_ON" >/dev/null'
tb "flows off (negative control): no door Service, no door NetworkPolicy, and the gateway holds no flows key and no 8002 port" 'door_check off <<< "$DOOR_OFF" >/dev/null'
FSK_RE='^  VEXA_FLOWS_SERVICE_KEY: "[A-Za-z0-9]{32,}"$'
tb "the chart's Secret renders a generated VEXA_FLOWS_SERVICE_KEY" '[ "$(grep -cE "$FSK_RE" <<< "$DOOR_ON")" -eq 1 ]'
# flows dials on Helm (config.v1 targets helm for flows-api): a set value lands in the flows Secret
# under the declared key; an unset one renders nothing, so the code default holds and flows.extraEnv
# still overrides it.
FDIALS_ON="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true \
  --set flows.limits.maxHops=4 --set flows.mail.captureSmtp=capture.example:2525 --set flows.nudges.enabled=true \
  --set flows.timelineKey=tl-key-for-render-only --show-only templates/flows.yaml)"
FDIALS_OFF="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --show-only templates/flows.yaml)"
tb "flows dials: set values render as their declared keys in the flows Secret" \
  'grep -qx "  VEXA_FLOWS_MAX_HOPS: \"4\"" <<< "$FDIALS_ON" && grep -qx "  VEXA_MAIL_CAPTURE_SMTP: \"capture.example:2525\"" <<< "$FDIALS_ON" && grep -qx "  VEXA_FLOWS_NUDGES: \"1\"" <<< "$FDIALS_ON" && grep -qx "  VEXA_FLOWS_TIMELINE_KEY: \"tl-key-for-render-only\"" <<< "$FDIALS_ON"'
tb "flows dials (control): unset dials render no key at all" \
  '! grep -qE "VEXA_(FLOWS_MAX_HOPS|MAIL_CAPTURE_SMTP|FLOWS_NUDGES|FLOWS_TIMELINE_KEY|MAIL_RELAY|GRAPH_CLIENT_SECRET):" <<< "$FDIALS_OFF"'
# flows egress (car 45): the flows Pods reach DNS, the gateway's service door, their database, the
# credential broker when the relay password is a reference, and the mail hosts, and nothing else.
# admin-api and crm-api admit their callers, not every chart Pod.
fe_check() { python3 -c '
import sys, yaml
mode = sys.argv[1]
docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
by = {(d["kind"], d["metadata"]["name"]): d for d in docs}
FLOWS = ["flows-api", "flows-mailbox", "flows-worker"]
def rules():
    np = by[("NetworkPolicy", "vexa-vexa-flows-egress")]
    sel = np["spec"]["podSelector"]
    assert sel["matchLabels"]["app.kubernetes.io/instance"] == "vexa", sel
    assert sorted(sel["matchExpressions"][0]["values"]) == FLOWS, sel
    assert np["spec"]["policyTypes"] == ["Egress"], np["spec"]
    out = []
    for r in np["spec"]["egress"]:
        ports = sorted((p["port"], p.get("protocol", "TCP")) for p in r.get("ports", []))
        assert ports, ("a flows egress rule with no ports opens every port", r)
        peers = []
        for t in r.get("to", []):
            if "podSelector" in t:
                peers.append("pod:" + t["podSelector"]["matchLabels"]["app.kubernetes.io/component"])
            else:
                peers.append("cidr:" + t["ipBlock"]["cidr"] + ("-private" if t["ipBlock"].get("except") else ""))
        out.append((tuple(sorted(peers)), tuple(ports)))
    return sorted(out)
def cp_excludes_flows():
    cp = by[("NetworkPolicy", "vexa-vexa-control-plane-egress")]["spec"]["podSelector"]["matchExpressions"][0]
    return cp["operator"] == "NotIn" and all(f in cp["values"] for f in FLOWS)
DNS = ((), (("53", "TCP"), ("53", "UDP")))
def dns(rs):
    return [((), tuple(sorted((str(p), t) for p, t in r[1]))) for r in rs if r[0] == ()]
MAILP = ((25, "TCP"), (143, "TCP"), (465, "TCP"), (587, "TCP"), (993, "TCP"))
if mode == "default":
    rs = rules()
    assert dns(rs) == [DNS], rs
    rest = [r for r in rs if r[0] != ()]
    want = sorted([(("pod:gateway",), ((8002, "TCP"),)), (("pod:postgres",), ((5432, "TCP"),)),
                   (("cidr:0.0.0.0/0-private", "cidr:::/0-private"), MAILP)])
    assert rest == want, rest
    assert cp_excludes_flows()
elif mode == "mail":
    rest = [r for r in rules() if r[0] != ()]
    assert (("cidr:203.0.113.10/32",), ((587, "TCP"), (993, "TCP"))) in rest, rest
    assert not any(p[0].startswith("cidr:0.0.0.0/0") for p, _ in rest), rest
elif mode == "extdb":
    rest = [r for r in rules() if r[0] != ()]
    assert (("cidr:10.20.0.5/32",), ((6432, "TCP"),)) in rest, rest
    assert not any("pod:postgres" in p for p, _ in rest), rest
elif mode == "extdb-open":
    rest = [r for r in rules() if r[0] != ()]
    assert (("cidr:0.0.0.0/0", "cidr:::/0"), ((6432, "TCP"),)) in rest, rest
elif mode == "broker":
    rest = [r for r in rules() if r[0] != ()]
    assert any(p == ("pod:credential-broker",) for p, _ in rest), rest
    bi = by[("NetworkPolicy", "vexa-vexa-credential-broker")]["spec"]["ingress"][0]["from"]
    assert any(sorted((f["podSelector"].get("matchExpressions") or [{}])[0].get("values", [])) == FLOWS for f in bi), bi
elif mode == "off":
    assert ("NetworkPolicy", "vexa-vexa-flows-egress") not in by, "networkPolicy.flows.enabled=false still renders the flows egress policy"
    assert not cp_excludes_flows(), "with the flows policy off, control-plane egress must cover the flows Pods"
elif mode == "ingress":
    crm = sys.argv[2] == "crm"
    def frm(name):
        np = by[("NetworkPolicy", name)]
        comps = []
        for r in np["spec"]["ingress"]:
            for f in r["from"]:
                ps = f["podSelector"]
                assert ps["matchLabels"]["app.kubernetes.io/instance"] == "vexa", ps
                comps += ps["matchExpressions"][0]["values"]
        return sorted(comps)
    want = ["agent-api", "dashboard", "gateway", "mcp", "meeting-api", "terminal"] + (["crm-api", "crm-outbox"] if crm else [])
    assert frm("vexa-vexa-admin-api-ingress") == sorted(want), frm("vexa-vexa-admin-api-ingress")
    if crm:
        assert frm("vexa-vexa-crm-api-ingress") == ["admin-api", "agent-api", "gateway", "mcp"], frm("vexa-vexa-crm-api-ingress")
print("ok")
' "$@"; }
FE=(-f "$CHART/values-test.yaml" --set flows.enabled=true --set flows.mail.enabled=true)
FE_ON="$(helm template vexa "$CHART" -n vexa "${FE[@]}")"
tb "flows egress: renders by default and allows exactly DNS, the gateway door (8002), postgres (5432) and the mail ports to public addresses; control-plane egress leaves the flows Pods out" 'fe_check default <<< "$FE_ON" >/dev/null'
tb "flows egress: networkPolicy.flows.mailEgress replaces the public mail fallback with the operator's CIDRs and ports" \
  'helm template vexa "$CHART" -n vexa "${FE[@]}" --set "networkPolicy.flows.mailEgress[0].cidr=203.0.113.10/32" --set "networkPolicy.flows.mailEgress[0].ports={993,587}" | fe_check mail >/dev/null'
tb "flows egress: an external database is reached on database.port at networkPolicy.flows.databaseEgress, not the in-chart postgres" \
  'helm template vexa "$CHART" -n vexa "${FE[@]}" --set postgres.enabled=false --set database.host=db.example.internal --set database.port=6432 --set "networkPolicy.flows.databaseEgress[0].cidr=10.20.0.5/32" | fe_check extdb >/dev/null'
tb "flows egress: an external database with no CIDR named is reached on database.port only" \
  'helm template vexa "$CHART" -n vexa "${FE[@]}" --set postgres.enabled=false --set database.host=db.example.internal --set database.port=6432 | fe_check extdb-open >/dev/null'
tb "flows egress: a relay password by reference adds the credential broker, and the broker admits the flows Pods" \
  'helm template vexa "$CHART" -n vexa "${FE[@]}" --set flows.mail.smtp.host=relay.example --set flows.mail.smtp.passwordRef=broker:mail/relay | fe_check broker >/dev/null'
tb "flows egress (control): networkPolicy.flows.enabled=false renders no flows egress policy and control-plane egress covers the flows Pods again" \
  'helm template vexa "$CHART" -n vexa "${FE[@]}" --set networkPolicy.flows.enabled=false | fe_check off >/dev/null'
tb "flows egress (control): networkPolicy.enabled=false renders no flows egress policy" \
  '! grep -q "name: vexa-vexa-flows-egress" <<< "$(helm template vexa "$CHART" -n vexa "${FE[@]}" --set networkPolicy.enabled=false)"'
tb "admin-api ingress admits its callers (gateway, terminal, dashboard, mcp, agent-api, meeting-api), not every chart Pod" 'fe_check ingress nocrm <<< "$FE_ON" >/dev/null'
tb "with the CRM on, admin-api also admits crm-api and crm-outbox, and crm-api admits gateway, mcp, agent-api and admin-api only" \
  'helm template vexa "$CHART" -n vexa "${FE[@]}" "${CRM[@]}" | fe_check ingress crm >/dev/null'
tb "flows egress: the OpenShift profile still renders with flows on, and carries the flows egress policy" \
  'helm template vexa "$CHART" -n vexa "${FE[@]}" -f "$CHART/values-openshift.yaml" --set agentApi.workspaces.storageClassName=rwx-class | fe_check default >/dev/null'
tb "flows.mail.authMode defaults to mta on the mailbox" 'grep -A1 "name: VEXA_FLOWS_MAIL_AUTH_MODE\$" <<< "$MB" | has -F "value: \"mta\""'
AUTHMODE_MB="$(helm template vexa "$CHART" -n vexa -f "$CHART/values-test.yaml" --set flows.enabled=true --set flows.mail.address=bot@corp.example --set flows.mail.existingSecret=mail-creds --set flows.mail.authMode=inapp --show-only templates/flows.yaml | awk '/^---/{p=0} /name: vexa-vexa-flows-mailbox$/{p=1} p')"
tb "flows.mail.authMode=inapp reaches the mailbox as VEXA_FLOWS_MAIL_AUTH_MODE" 'grep -A1 "name: VEXA_FLOWS_MAIL_AUTH_MODE\$" <<< "$AUTHMODE_MB" | has -F "value: \"inapp\""'
[ "$fail" -eq 0 ] && { echo "gate:helm PASS"; exit 0; } || { echo "gate:helm FAIL"; exit 1; }
