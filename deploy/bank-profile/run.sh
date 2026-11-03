#!/usr/bin/env bash
# =============================================================================
# deploy/bank-profile/run.sh — the bank profile: Vexa installed and used the way a bank's cluster
# allows, on a kind cluster of its own. bank-ready = this run green on the exact release sha.
#
#   run.sh all              up → for each database variant (a, b): db → install → check → journey
#                           → uninstall → down. Exit 0 only if every step of both variants passed.
#   run.sh up               kind cluster on its own docker network, Calico (enforces NetworkPolicy),
#                           images loaded, the bank's platform (PSA restricted + restricted-v2 SCC
#                           webhook, egress default-deny, a namespace-scoped deployer) and the bank's
#                           services (Postgres, S3, model, STT, IdP), all TLS under a corporate CA
#   run.sh db a|b           reset the bank's database for variant a (app user has CREATEROLE only) or
#                           b (the DB team pre-creates the roles; the app user has no role privilege)
#   run.sh install a|b      helm install AS THE DEPLOYER (no cluster rights), values-openshift + bank
#   run.sh check            posture: every Pod's uid/caps/seccomp/RO-rootfs, the deployer's reach,
#                           egress allowed only to declared endpoints, the CA doing the trusting
#   run.sh journey          first OIDC sign-in → workspace → one chat turn on the stub model →
#                           a meeting bot (mock) to active with a transcript segment
#   run.sh uninstall        helm uninstall as the deployer, and what it leaves
#   run.sh down             delete the cluster and its docker network
#
# Env: IMAGE_TAG (required: vexaai/vexa-*:<tag> and vexaai/mock-bot:<tag> present locally) ·
#      BANK_CLUSTER (bank-profile) · BANK_SUBNET (10.161.61.0/24, its own docker network) ·
#      BANK_POD_CIDR (10.162.0.0/16) · BANK_SVC_CIDR (10.163.0.0/16) · BANK_NODE_CPUS (8) ·
#      BANK_NODE_MEM (24g) · BANK_WORK (~/.cache/vexa-bank-profile/<cluster>) · KEEP=1 (all: no down)
# Needs: docker, kind (0.23+), kubectl, helm, openssl, python3 with PyYAML, curl.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CHART="$ROOT/deploy/helm/charts/vexa"
CLUSTER="${BANK_CLUSTER:-bank-profile}"
NET="$CLUSTER"
SUBNET="${BANK_SUBNET:-10.161.61.0/24}"
POD_CIDR="${BANK_POD_CIDR:-10.162.0.0/16}"
SVC_CIDR="${BANK_SVC_CIDR:-10.163.0.0/16}"
WORK="${BANK_WORK:-$HOME/.cache/vexa-bank-profile/$CLUSTER}"
STUB_IMAGE="${BANK_STUB_IMAGE:-python:3.12-slim}"
PG_IMAGE="${BANK_PG_IMAGE:-postgres:16}"
CALICO="${BANK_CALICO_VERSION:-v3.28.2}"
USER_EMAIL="profile.user@bank.example"
NS=vexa
ADMIN_KC="$WORK/admin.kubeconfig"
DEPLOYER_KC="$WORK/deployer.kubeconfig"
REPORT="$WORK/report.txt"
mkdir -p "$WORK"
chmod 700 "$WORK"

log()  { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
pass() { printf 'PASS  %-34s %s\n' "$1" "$2" | tee -a "$REPORT"; }
fail() { printf 'FAIL  %-34s %s\n' "$1" "$2" | tee -a "$REPORT"; FAILED=1; }
FAILED=0
k()  { kubectl --kubeconfig "$ADMIN_KC" "$@"; }
kd() { kubectl --kubeconfig "$DEPLOYER_KC" -n "$NS" "$@"; }
secret() { # a value generated once per cluster and kept in the work dir (mode 600)
  local f="$WORK/secret.$1"
  [ -s "$f" ] || (umask 077; openssl rand -hex 24 > "$f")
  cat "$f"
}
images() {
  local t="${IMAGE_TAG:?IMAGE_TAG is required}"
  printf '%s\n' "vexaai/vexa-gateway:$t" "vexaai/vexa-admin-api:$t" "vexaai/vexa-meeting-api:$t" \
    "vexaai/vexa-runtime:$t" "vexaai/vexa-agent-api:$t" "vexaai/vexa-agent-worker:$t" "vexaai/vexa-mcp:$t" \
    "vexaai/vexa-terminal:$t" "vexaai/vexa-credential-broker:$t" "vexaai/vexa-crm:$t" "vexaai/mock-bot:$t" \
    "$STUB_IMAGE" "$PG_IMAGE"
}

# ── up ────────────────────────────────────────────────────────────────────────────────────────────
subnet_free() {
  local taken
  # shellcheck disable=SC2046  # one argument per network id
  taken="$(docker network inspect $(docker network ls -q) -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null | tr ' ' '\n' | grep -v '^$' || true)"
  python3 - "$SUBNET" "$POD_CIDR" "$SVC_CIDR" <<PY
import ipaddress, sys
mine = [ipaddress.ip_network(c) for c in sys.argv[1:]]
taken = [ipaddress.ip_network(c) for c in """$taken""".split() if ":" not in c]
routes = """$(ip -4 route 2>/dev/null | awk '{print $1}' | grep -E '^[0-9]+\.' || true)""".split()
taken += [ipaddress.ip_network(r, strict=False) for r in routes if "/" in r]
bad = [(m, t) for m in mine for t in taken if m.overlaps(t)]
if bad:
    sys.exit("CIDR in use: " + ", ".join(f"{m} overlaps {t}" for m, t in bad))
PY
}

cmd_up() {
  : > "$REPORT"
  if ! docker network inspect "$NET" >/dev/null 2>&1; then
    subnet_free
    docker network create --subnet "$SUBNET" "$NET" >/dev/null
  fi
  if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    cat > "$WORK/kind.yaml" <<YAML
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  disableDefaultCNI: true        # kindnet does not enforce NetworkPolicy; Calico does
  podSubnet: "$POD_CIDR"
  serviceSubnet: "$SVC_CIDR"
nodes:
  - role: control-plane
YAML
    rm -f "$WORK/scc-uid"     # a new cluster draws a new uid range, as a new OpenShift project does
    KIND_EXPERIMENTAL_DOCKER_NETWORK="$NET" kind create cluster --name "$CLUSTER" \
      --config "$WORK/kind.yaml" --kubeconfig "$ADMIN_KC" --wait 0s
  fi
  docker update --cpus "${BANK_NODE_CPUS:-8}" --memory "${BANK_NODE_MEM:-24g}" \
    --memory-swap "${BANK_NODE_MEM:-24g}" "$CLUSTER-control-plane" >/dev/null
  [ -s "$WORK/calico.yaml" ] || curl -fsSL -o "$WORK/calico.yaml" \
    "https://raw.githubusercontent.com/projectcalico/calico/$CALICO/manifests/calico.yaml"
  sed -e "s|# - name: CALICO_IPV4POOL_CIDR|- name: CALICO_IPV4POOL_CIDR|" \
      -e "s|#   value: \"192.168.0.0/16\"|  value: \"$POD_CIDR\"|" "$WORK/calico.yaml" | k apply -f - >/dev/null
  k -n kube-system rollout status ds/calico-node --timeout=600s
  k wait --for=condition=Ready node --all --timeout=300s
  log "loading images"
  for i in $(images); do
    docker image inspect "$i" >/dev/null 2>&1 || docker pull -q "$i" >/dev/null
    kind load docker-image --name "$CLUSTER" "$i" >/dev/null
  done
  certs
  local apiserver uid ro
  apiserver="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" "$CLUSTER-control-plane")"
  echo "$apiserver" > "$WORK/apiserver"
  [ -s "$WORK/scc-uid" ] || echo $(( 1000000000 + (RANDOM % 9000) * 10000 )) > "$WORK/scc-uid"
  uid="$(cat "$WORK/scc-uid")"
  ro="$(ro_containers)"
  echo "$ro" > "$WORK/scc-ro"
  log "restricted-v2: uid range $uid/10000; read-only root fs for: ${ro:-none}"
  sed -e "s|__SCC_UID__|$uid|g" -e "s|__SCC_RO__|$ro|g" -e "s|__STUB_IMAGE__|$STUB_IMAGE|g" \
      -e "s|__CA_BUNDLE__|$(base64 -w0 < "$WORK/ca.crt")|g" "$HERE/manifests/platform.yaml" > "$WORK/platform.yaml"
  # namespaces first: the webhook's own objects live in one
  python3 -c 'import sys,yaml; print("---\n".join(yaml.safe_dump(d) for d in yaml.safe_load_all(open(sys.argv[1])) if d and d["kind"]=="Namespace"))' \
    "$WORK/platform.yaml" | k apply -f - >/dev/null
  for ns in bank-platform bank-ext bank-users; do
    k -n "$ns" create configmap bank-stub --from-file="$HERE/stub/stub.py" --from-file="$HERE/stub/journey.py" \
      --dry-run=client -o yaml | k apply -f - >/dev/null
    k -n "$ns" create configmap corp-root-ca --from-file=ca.crt="$WORK/ca.crt" --dry-run=client -o yaml | k apply -f - >/dev/null
  done
  k -n bank-platform create secret tls scc-tls --cert "$WORK/scc.crt" --key "$WORK/scc.key" --dry-run=client -o yaml | k apply -f - >/dev/null
  for s in model stt idp vexa; do
    k -n bank-ext create secret tls "$s-tls" --cert "$WORK/$s.crt" --key "$WORK/$s.key" --dry-run=client -o yaml | k apply -f - >/dev/null
  done
  k -n bank-ext create secret generic idp-signing-key --from-file=key.pem="$WORK/idp-signing.pem" --dry-run=client -o yaml | k apply -f - >/dev/null
  k -n bank-ext create secret generic idp-client --from-literal=client-secret="$(secret oidc-client)" --dry-run=client -o yaml | k apply -f - >/dev/null
  k -n bank-db create secret generic pg-superuser --from-literal=password="$(secret pg-superuser)" --dry-run=client -o yaml | k apply -f - >/dev/null
  k -n bank-ext create secret generic s3-fixture-credentials --from-literal=AWS_ACCESS_KEY_ID="$(secret s3-id | cut -c1-20)" \
    --from-literal=AWS_SECRET_ACCESS_KEY="$(secret s3-key)" --dry-run=client -o yaml | k apply -f - >/dev/null
  k apply -f "$WORK/platform.yaml" >/dev/null
  sed -e "s|__STUB_IMAGE__|$STUB_IMAGE|g" -e "s|__PG_IMAGE__|$PG_IMAGE|g" -e "s|__USER_EMAIL__|$USER_EMAIL|g" \
    "$HERE/manifests/bank-ext.yaml" | k apply -f - >/dev/null
  k -n bank-ext apply -f "$ROOT/deploy/helm/tests/s3-fixture.yaml" >/dev/null
  sed -e 's/s3-fixture-bucket/s3-sessions-bucket/g' -e 's/--bucket vexa-recordings/--bucket vexa-sessions/' \
    "$ROOT/deploy/helm/tests/s3-fixture.yaml" | python3 -c 'import sys,yaml; print(yaml.safe_dump([d for d in yaml.safe_load_all(sys.stdin) if d and d["kind"]=="Job"][0]))' \
    | k -n bank-ext apply -f - >/dev/null
  k -n bank-platform rollout status deploy/scc --timeout=300s
  for d in model stt idp vexa s3-fixture; do k -n bank-ext rollout status "deploy/$d" --timeout=300s; done
  k -n bank-db rollout status deploy/pg --timeout=300s
  k -n bank-ext wait --for=condition=complete job/s3-fixture-bucket job/s3-sessions-bucket --timeout=300s
  k -n bank-users wait --for=condition=Ready pod/workstation --timeout=300s
  deployer_kubeconfig
  log "up: cluster $CLUSTER on $NET ($SUBNET), apiserver $apiserver"
}

certs() { # the corporate CA and every certificate it issues; each made once per work dir
  (
    cd "$WORK"; umask 077
    [ -s ca.crt ] || openssl req -x509 -newkey rsa:2048 -nodes -days 7 -keyout ca.key -out ca.crt \
      -subj "/O=Bank Profile/CN=Bank Profile Corporate Root CA" \
      -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
    issue() { # name dns...
      local n="$1"; shift
      [ -s "$n.crt" ] && return 0
      local san; san="$(printf 'DNS:%s,' "$@")"; san="${san%,}"
      openssl req -newkey rsa:2048 -nodes -keyout "$n.key" -out "$n.csr" -subj "/O=Bank Profile/CN=$1" 2>/dev/null
      printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid,issuer\nsubjectKeyIdentifier=hash\n' "$san" > "$n.ext"
      openssl x509 -req -in "$n.csr" -CA ca.crt -CAkey ca.key -CAcreateserial -days 7 -extfile "$n.ext" -out "$n.crt" 2>/dev/null
    }
    for s in model stt idp vexa; do issue "$s" "$s.bank-ext.svc.cluster.local" "$s.bank-ext.svc" "$s.bank-ext"; done
    issue scc scc.bank-platform.svc scc.bank-platform.svc.cluster.local
    [ -s idp-signing.pem ] || openssl genrsa -traditional -out idp-signing.pem 2048 2>/dev/null
    chmod 644 ./*.crt
  )
}

ro_containers() { # the containers the chart itself renders with a read-only root filesystem
  helm template "$NS" "$CHART" -n "$NS" -f "$CHART/values-openshift.yaml" -f "$(bank_values)" \
    --set global.securityContext.deliver=true --set global.imageTag="$IMAGE_TAG" \
    --set secrets.internalApiSecret=render-only --set "signals.enabled=true" 2>/dev/null \
  | python3 -c '
import sys, yaml
names = set()
for d in yaml.safe_load_all(sys.stdin):
    spec = ((d or {}).get("spec") or {}).get("template", {}).get("spec") or {}
    for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        if (c.get("securityContext") or {}).get("readOnlyRootFilesystem"):
            names.add(c["name"])
print(",".join(sorted(names)))'
}

bank_values() { # values-bank.yaml with this cluster's API server address filled in
  local apiserver; apiserver="$(cat "$WORK/apiserver" 2>/dev/null || echo 127.0.0.1)"
  sed "s|__APISERVER__|$apiserver|" "$HERE/values-bank.yaml" > "$WORK/values-bank.yaml"
  echo "$WORK/values-bank.yaml"
}

deployer_kubeconfig() {
  local token server ca
  for _ in $(seq 1 30); do
    token="$(k -n "$NS" get secret bank-deployer-token -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || true)"
    [ -n "$token" ] && break; sleep 1
  done
  server="$(k config view --raw -o jsonpath='{.clusters[0].cluster.server}')"
  ca="$(k config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
  (umask 077; cat > "$DEPLOYER_KC" <<KC
apiVersion: v1
kind: Config
clusters: [{name: bank, cluster: {server: "$server", certificate-authority-data: "$ca"}}]
users: [{name: bank-deployer, user: {token: "$token"}}]
contexts: [{name: bank, context: {cluster: bank, user: bank-deployer, namespace: $NS}}]
current-context: bank
KC
  )
}

# ── db ────────────────────────────────────────────────────────────────────────────────────────────
psql_su() { k -n bank-db exec -i deploy/pg -- psql -v ON_ERROR_STOP=1 -U postgres -qAt "$@"; }

cmd_db() {
  local v="${1:?a or b}" app; app="$(secret db-app)"
  psql_su -d postgres <<SQL
DROP DATABASE IF EXISTS vexa WITH (FORCE);
DROP DATABASE IF EXISTS flows WITH (FORCE);
DROP ROLE IF EXISTS vexa_agent_sessions;
DROP ROLE IF EXISTS vexa_crm;
DROP ROLE IF EXISTS vexa;
CREATE ROLE vexa LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$app';
CREATE DATABASE vexa OWNER vexa;
SQL
  if [ "$v" = a ]; then
    # Way 1 of the install guide, Postgres 16: the app user may create roles, nothing more.
    psql_su -d postgres -c "ALTER ROLE vexa CREATEROLE;"
  else
    # Way 2: the DB team creates the two roles with the passwords in the deployer's Secrets.
    psql_su -d postgres <<SQL
CREATE ROLE vexa_agent_sessions LOGIN NOINHERIT PASSWORD '$(secret sessions-role)';
CREATE ROLE vexa_crm LOGIN NOINHERIT PASSWORD '$(secret crm-role)';
SQL
  fi
  log "db $v: $(psql_su -d postgres -c "SELECT string_agg(rolname||'(super='||rolsuper||',createrole='||rolcreaterole||',createdb='||rolcreatedb||')', ' ' ORDER BY rolname) FROM pg_roles WHERE rolname LIKE 'vexa%'")"
  echo "$v" > "$WORK/variant"
}

# ── install ───────────────────────────────────────────────────────────────────────────────────────
cmd_install() {
  local v="${1:-$(cat "$WORK/variant")}" extra=() t0
  local s3id s3key; s3id="$(secret s3-id | cut -c1-20)"; s3key="$(secret s3-key)"
  # Everything below runs as the namespace-scoped deployer.
  kd create configmap corp-root-ca --from-file=ca.crt="$WORK/ca.crt" --dry-run=client -o yaml | kd apply -f - >/dev/null
  kd create secret generic bank-db-app --from-literal=POSTGRES_DB=vexa --from-literal=POSTGRES_USER=vexa \
    --from-literal=POSTGRES_PASSWORD="$(secret db-app)" --dry-run=client -o yaml | kd apply -f - >/dev/null
  for s in recordings sessions; do
    kd create secret generic "bank-s3-$s" --from-literal=AWS_ACCESS_KEY_ID="$s3id" \
      --from-literal=AWS_SECRET_ACCESS_KEY="$s3key" --dry-run=client -o yaml | kd apply -f - >/dev/null
  done
  kd create secret generic bank-oidc --from-literal=client-secret="$(secret oidc-client)" --dry-run=client -o yaml | kd apply -f - >/dev/null
  if [ "$v" = b ]; then
    kd create secret generic bank-sessions-db --from-literal=password="$(secret sessions-role)" --dry-run=client -o yaml | kd apply -f - >/dev/null
    kd create secret generic bank-crm-db --from-literal=password="$(secret crm-role)" --dry-run=client -o yaml | kd apply -f - >/dev/null
    extra=(--set agentApi.sessions.existingSecret=bank-sessions-db --set crm.existingSecret=bank-crm-db)
  fi
  t0=$(date +%s)
  if helm --kubeconfig "$DEPLOYER_KC" upgrade --install "$NS" "$CHART" -n "$NS" \
      -f "$CHART/values-openshift.yaml" -f "$(bank_values)" \
      --set global.imageTag="$IMAGE_TAG" --set runtime.browserImage="vexaai/mock-bot:$IMAGE_TAG" \
      --set secrets.internalApiSecret="$(secret internal-api)" "${extra[@]}" \
      --wait --timeout 15m > "$WORK/helm-$v.log" 2>&1; then
    pass "[$v] helm install (deployer)" "deployed in $(( $(date +%s) - t0 ))s as system:serviceaccount:$NS:bank-deployer"
  else
    fail "[$v] helm install (deployer)" "$(tail -3 "$WORK/helm-$v.log" | tr '\n' ' ')"
    k -n "$NS" get pods -o wide >&2 || true
    k -n "$NS" get events --sort-by=.lastTimestamp 2>/dev/null | tail -25 >&2 || true
    return 1
  fi
  local jobs; jobs="$(k -n "$NS" get jobs -o name)"
  for j in $jobs; do
    local line; line="$(k -n "$NS" logs "$j" --all-containers 2>/dev/null | grep -E 'session-schema|crm-schema|STOP' | tail -1)"
    [ -n "$line" ] && pass "[$v] ${j#job.batch/}" "$line"
  done
  psql_su -d vexa -c "SELECT 'roles: '||string_agg(rolname||' super='||rolsuper||' createrole='||rolcreaterole||' inherit='||rolinherit, '; ' ORDER BY rolname) FROM pg_roles WHERE rolname LIKE 'vexa%'" | tee -a "$REPORT"
}

# ── check ─────────────────────────────────────────────────────────────────────────────────────────
cmd_check() {
  local v; v="$(cat "$WORK/variant" 2>/dev/null || echo '?')"
  local uid ro; uid="$(cat "$WORK/scc-uid")"; ro="$(cat "$WORK/scc-ro")"
  # 1. every Pod in the release namespace, as admitted
  local out
  if out="$(k -n "$NS" get pods -o json | python3 -c '
import json, sys
uid, ro = int(sys.argv[1]), set(filter(None, sys.argv[2].split(",")))
bad, n, names = [], 0, []
for p in json.load(sys.stdin)["items"]:
    if p["status"].get("phase") in ("Succeeded", "Failed"):
        continue
    n += 1; pn = p["metadata"]["name"]; names.append(pn)
    psc = p["spec"].get("securityContext") or {}
    if (psc.get("seccompProfile") or {}).get("type") != "RuntimeDefault": bad.append(pn + ": pod seccomp")
    if not psc.get("runAsNonRoot"): bad.append(pn + ": runAsNonRoot")
    for c in p["spec"]["containers"] + p["spec"].get("initContainers", []):
        sc = c.get("securityContext") or {}
        w = pn + "/" + c["name"]
        if sc.get("runAsUser") != uid: bad.append(f"{w}: uid {sc.get('runAsUser')}")
        if sc.get("allowPrivilegeEscalation") is not False: bad.append(f"{w}: privilege escalation")
        if (sc.get("capabilities") or {}).get("drop") != ["ALL"] or (sc.get("capabilities") or {}).get("add"): bad.append(f"{w}: capabilities")
        if c["name"] in ro and not sc.get("readOnlyRootFilesystem"): bad.append(f"{w}: root fs writable")
    for s in p["status"].get("containerStatuses") or []:
        if not s.get("ready"): bad.append(pn + "/" + s["name"] + ": not ready")
print(("; ".join(bad) if bad else f"{n} pods, every container uid {uid}, non-root, no escalation, caps drop ALL, seccomp RuntimeDefault; read-only root fs: {sorted(ro)}"))
sys.exit(1 if bad else 0)' "$uid" "$ro")"; then pass "[$v] restricted-v2 posture" "$out"; else fail "[$v] restricted-v2 posture" "$out"; fi
  # a privileged Pod is refused in the namespace (the policy is live, not decorative)
  if out="$(kd run bank-negative --image="$STUB_IMAGE" --restart=Never --privileged --command -- true 2>&1)"; then
    kd delete pod bank-negative --wait=false >/dev/null 2>&1; fail "[$v] negative: privileged Pod" "was admitted"
  else pass "[$v] negative: privileged Pod" "refused: $(echo "$out" | grep -oE 'restricted-v2[^;]*|violates PodSecurity[^:]*' | head -1)"; fi
  # 2. the deployer's reach
  local can=()
  for q in "create clusterroles" "create namespaces" "get nodes" "list pods -n kube-system" "create mutatingwebhookconfigurations" "get secrets -n bank-db"; do
    # shellcheck disable=SC2086
    [ "$(kubectl --kubeconfig "$DEPLOYER_KC" auth can-i $q 2>/dev/null)" = yes ] && can+=("$q")
  done
  if [ ${#can[@]} -eq 0 ]; then pass "[$v] deployer is namespace-scoped" "no cluster-scoped or other-namespace right (6 probes)"
  else fail "[$v] deployer is namespace-scoped" "can: ${can[*]}"; fi
  # the network itself reaches the internet: only the policy stands between a Vexa Pod and it
  if out="$(k -n bank-users exec workstation -- python3 -c 'import socket; ip=socket.getaddrinfo("api.anthropic.com",443,socket.AF_INET)[0][4][0]; socket.create_connection((ip,443),timeout=8).close(); print("reached api.anthropic.com", ip, "from bank-users (no egress policy there)")' 2>&1)"
  then pass "[$v] control: egress without policy" "$out"; else fail "[$v] control: egress without policy" "$(echo "$out" | tail -1)"; fi
  # 3. egress and the CA, from inside meeting-api (a Python image with the chart's CA bundle env)
  if out="$(k -n "$NS" exec -i deploy/vexa-meeting-api -c meeting-api -- python3 - <<'PY' 2>&1
import os, socket, ssl, urllib.request
ok = True
def get(url, ctx=None):
    return urllib.request.urlopen(url, timeout=6, context=ctx).status
for url in ("https://stt.bank-ext.svc.cluster.local/health", "https://model.bank-ext.svc.cluster.local/v1/models",
            "https://idp.bank-ext.svc.cluster.local/adfs/.well-known/openid-configuration"):
    try: print("allowed+trusted", url, get(url))
    except Exception as e: ok = False; print("FAILED", url, type(e).__name__, e)
# without the corporate CA the same endpoint is not trusted: the CA is what makes it work
plain = ssl.create_default_context(cafile="/etc/ssl/certs/ca-certificates.crt")
try: get("https://model.bank-ext.svc.cluster.local/v1/models", plain); ok = False; print("UNEXPECTED trusted without the corp CA")
except Exception as e: print("untrusted without corp CA:", type(e).__name__)
for host in ("api.anthropic.com", "api.openai.com", "1.1.1.1"):
    try: ip = socket.getaddrinfo(host, 443, socket.AF_INET)[0][4][0]
    except Exception as e: ok = False; print("UNRESOLVED", host, e); continue
    try:
        socket.create_connection((ip, 443), timeout=5).close(); ok = False; print("UNEXPECTED egress to", host, ip)
    except Exception as e: print("blocked", host, ip, type(e).__name__)
print("SSL_CERT_FILE=" + os.environ.get("SSL_CERT_FILE", ""))
raise SystemExit(0 if ok else 1)
PY
)"; then pass "[$v] egress allowlist + corp CA" "$(echo "$out" | tr '\n' ' ' | cut -c1-600)"
  else fail "[$v] egress allowlist + corp CA" "$(echo "$out" | tr '\n' ' ' | cut -c1-600)"; fi
}

# ── journey ───────────────────────────────────────────────────────────────────────────────────────
ws() { # step → run the user's side of a step from the workstation
  k -n bank-users exec workstation -- env TERMINAL="https://vexa.bank-ext.svc.cluster.local" \
    GATEWAY="http://vexa-gateway.$NS.svc.cluster.local:8000" KEY="${KEY:-}" EXPECT_EMAIL="$USER_EMAIL" \
    python3 /stub/journey.py "$1"
}

cmd_journey() {
  local v; v="$(cat "$WORK/variant" 2>/dev/null || echo '?')"
  local out step admin pf port uid since; since="$(date -u +%FT%TZ)"
  KEY=""          # minted below for the person who signs in on THIS install
  for step in ${BANK_STEPS:-signin workspace chat meeting}; do
    if [ "$step" != signin ] && [ -z "${KEY:-}" ]; then
      admin="$(k -n "$NS" get secret vexa-secrets -o jsonpath='{.data.ADMIN_API_TOKEN}' | base64 -d)"
      port=$(( 20000 + RANDOM % 20000 ))
      k -n "$NS" port-forward svc/vexa-admin-api "$port:8001" >/dev/null 2>&1 & pf=$!
      for _ in $(seq 1 20); do curl -s -o /dev/null "http://127.0.0.1:$port/health" && break; sleep 1; done
      uid="$(curl -fsS -H "X-Admin-API-Key: $admin" "http://127.0.0.1:$port/admin/users/email/$USER_EMAIL" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' 2>/dev/null || true)"
      if [ -n "$uid" ]; then pass "[$v] user created by first sign-in" "admin-api user id $uid for $USER_EMAIL"
      else fail "[$v] user created by first sign-in" "admin-api has no user $USER_EMAIL"; kill $pf; return 1; fi
      KEY="$(curl -fsS -H "X-Admin-API-Key: $admin" -X POST \
        "http://127.0.0.1:$port/admin/users/$uid/tokens?scopes=bot,tx,browser" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
      kill $pf 2>/dev/null || true
      export KEY
    fi
    if out="$(ws "$step" 2>&1)"; then pass "[$v] $step" "$(echo "$out" | grep '^RESULT' | cut -d' ' -f4- | cut -c1-400)"
    else
      fail "[$v] $step" "$(echo "$out" | tail -8 | tr '\n' ' ' | cut -c1-900)"
      [ "$step" = signin ] && return 1
    fi
  done
  # the Pods the runtime spawned (the meeting bot, the agent worker) went through the same admission
  local spawned; spawned="$(k -n bank-platform logs deploy/scc --since-time="$since" 2>/dev/null | grep -oE "(ADMIT|DENY) $NS/$NS-(mtg|agent)-[^ ]+ [^ ]+ [0-9]*" | sort -u | tr '\n' ';')"
  if echo "$spawned" | grep -q "ADMIT $NS/$NS-mtg-" && echo "$spawned" | grep -q "ADMIT $NS/$NS-agent-" && ! echo "$spawned" | grep -q DENY; then
    pass "[$v] spawned Pods under restricted-v2" "$spawned"
  else fail "[$v] spawned Pods under restricted-v2" "${spawned:-no bot or worker Pod admitted}"; fi
  local calls; calls="$(k -n bank-ext logs deploy/model --since-time="$since" 2>/dev/null | grep -c 'POST /v1/chat/completions' || true)"
  if [ "${calls:-0}" -gt 0 ]; then pass "[$v] model calls stayed in-cluster" "$calls chat/completions request(s) at the bank's model stub"
  else fail "[$v] model calls stayed in-cluster" "the model stub saw no request"; fi
}

cmd_uninstall() {
  helm --kubeconfig "$DEPLOYER_KC" uninstall "$NS" -n "$NS" --wait --timeout 5m >/dev/null 2>&1 || true
  # what the chart leaves behind: PVCs (kept by design), the runtime's per-workload Secrets and Pods
  kd delete pods -l runtime.managed=true --ignore-not-found --wait=false >/dev/null 2>&1 || true
  kd delete pvc --all --wait=false >/dev/null 2>&1 || true
  kd delete jobs --all >/dev/null 2>&1 || true
  for s in $(kd get secrets -o name | grep -v bank-deployer-token); do kd delete "$s" >/dev/null 2>&1 || true; done
  for _ in $(seq 1 60); do [ -z "$(kd get pods -o name 2>/dev/null)" ] && break; sleep 2; done
}

cmd_down() {
  kind delete cluster --name "$CLUSTER" --kubeconfig "$ADMIN_KC" 2>/dev/null || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  log "down: cluster $CLUSTER and network $NET removed (images stay; work dir $WORK)"
}

cmd_all() {
  cmd_up
  for v in a b; do
    log "── variant $v ──"
    cmd_db "$v"
    if cmd_install "$v"; then cmd_check; cmd_journey || true; fi
    cmd_uninstall
  done
  [ "${KEEP:-0}" = 1 ] || cmd_down
  echo; cat "$REPORT"
  if grep -q '^FAIL' "$REPORT"; then echo "bank profile: RED"; exit 1; fi
  echo "bank profile: GREEN"
}

case "${1:-}" in
  up) cmd_up ;; db) cmd_db "${2:-}" ;; install) cmd_install "${2:-}" ;; check) cmd_check ;;
  journey) cmd_journey ;; uninstall) cmd_uninstall ;; down) cmd_down ;; all) cmd_all ;;
  *) sed -n '2,27p' "$0"; exit 2 ;;
esac
[ "$FAILED" = 0 ]
