#!/usr/bin/env bash
# Deploy vexa helm chart on a freshly provisioned LKE cluster.
# Reads: .state/lke_kubeconfig_path, .state/lke_node_ip
# Reads local: .env (for TRANSCRIPTION_SERVICE_URL + TOKEN)
set -euo pipefail
source "$(dirname "$0")/common.sh"

export KUBECONFIG=$(state_read lke_kubeconfig_path)
NODE_IP=$(state_read lke_node_ip)
CHART_PATH="$ROOT/deploy/helm/charts/vexa"
VALUES_FILE="$CHART_PATH/values-test.yaml"

echo ""
echo "  lke-setup-helm"
echo "  ──────────────────────────────────────────────"

# ── 0. Clear stale credentials ──────────────────
rm -f "$STATE/admin_token" "$STATE/api_token"

# ── 1. Read config from central .env ───────────────
# Single source of truth: deploy/env-example → .env
TX_URL=$(grep -E '^TRANSCRIPTION_SERVICE_URL=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || echo "")
TX_TOKEN=$(grep -E '^TRANSCRIPTION_SERVICE_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || echo "")
IMAGE_TAG=$(grep -E '^IMAGE_TAG=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || echo "")

if [ -n "$TX_URL" ]; then
    pass ".env: TX_URL=${TX_URL:0:40}..."
else
    info "no TRANSCRIPTION_SERVICE_URL in .env — transcription checks will skip"
fi
if [ -n "$IMAGE_TAG" ]; then
    info ".env: IMAGE_TAG=$IMAGE_TAG"
fi

# ── 2. Verify cluster reachable ──────────────────
info "verifying cluster..."
kubectl cluster-info 2>&1 | head -1
NODES=$(kubectl get nodes --no-headers | wc -l)
pass "cluster: $NODES nodes"

# ── 3. Helm install (phase 1 — without VEXA_API_KEY) ─
info "installing vexa chart (this pulls images — may take 2-5 minutes)..."

GATEWAY_URL="http://${NODE_IP}:30056"
DASHBOARD_URL="http://${NODE_IP}:30001"
ADMIN_URL="$GATEWAY_URL"

HELM_ARGS=(
    --values "$VALUES_FILE"
    --set "dashboard.env.VEXA_PUBLIC_API_URL=$GATEWAY_URL"
    --set "dashboard.env.NEXT_PUBLIC_APP_URL=$DASHBOARD_URL"
)

if [ -n "$IMAGE_TAG" ]; then
    HELM_ARGS+=(--set "global.imageTag=$IMAGE_TAG")
    info "using global.imageTag=$IMAGE_TAG"
fi

if [ -n "$TX_URL" ]; then
    HELM_ARGS+=(
        --set "meetingApi.transcriptionServiceUrl=$TX_URL"
        --set "meetingApi.transcriptionServiceToken=$TX_TOKEN"
    )
fi

helm upgrade --install vexa "$CHART_PATH" "${HELM_ARGS[@]}" --wait --timeout 10m 2>&1 | tail -5
pass "helm install succeeded"

# ── 4. Wait for all deployments ──────────────────
info "waiting for deployments..."
for i in $(seq 1 30); do
    NOT_READY=$(kubectl get deploy -l app.kubernetes.io/name=vexa --no-headers 2>/dev/null | \
        awk '{split($2,a,"/"); if(a[1]!=a[2]) print $1}')
    if [ -z "$NOT_READY" ]; then
        pass "all deployments ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        fail "deployments not ready after 5 minutes: $NOT_READY"
        kubectl get deploy -l app.kubernetes.io/name=vexa
        exit 1
    fi
    info "waiting: $NOT_READY ($i/30)..."
    sleep 10
done

# ── 5. Verify endpoints via NodePort ─────────────
for ep in "$GATEWAY_URL|gateway" "$DASHBOARD_URL|dashboard"; do
    URL=${ep%%|*}
    NAME=${ep##*|}
    CODE=$(curl -sf -o /dev/null -w '%{http_code}' --connect-timeout 10 "$URL" 2>/dev/null || echo "000")
    if [ "$CODE" = "200" ]; then
        pass "$NAME: $URL"
    else
        fail "$NAME: HTTP $CODE at $URL"
    fi
done

# ── 6. Verify MinIO bucket ───────────────────────
info "verifying MinIO bucket..."
for attempt in 1 2 3; do
    BUCKET_OK=$(kubectl exec deploy/vexa-vexa-meeting-api -- python3 -c "
import os,boto3
ep=os.environ['MINIO_ENDPOINT']
s3=boto3.client('s3',endpoint_url=f'http://{ep}',
    aws_access_key_id=os.environ['MINIO_ACCESS_KEY'],
    aws_secret_access_key=os.environ['MINIO_SECRET_KEY'])
bk=os.environ.get('MINIO_BUCKET','vexa')
try: s3.head_bucket(Bucket=bk); print('OK')
except: s3.create_bucket(Bucket=bk); print('CREATED')
" 2>/dev/null)
    if echo "$BUCKET_OK" | grep -qE "OK|CREATED"; then
        pass "MinIO bucket: vexa ($BUCKET_OK)"
        break
    fi
    if [ "$attempt" -eq 3 ]; then
        info "MinIO bucket check inconclusive — smoke will verify via MINIO_BUCKET_WRITABLE"
    fi
    sleep 5
done

# ── 7. Bootstrap API token ──────────────────────
info "bootstrapping credentials..."
ADMIN_TOKEN=$(kubectl exec deploy/vexa-vexa-admin-api -- printenv ADMIN_API_TOKEN 2>/dev/null)

# Find or create test user
USER_ID=$(curl -sf "$GATEWAY_URL/admin/users/email/test@vexa.ai" \
    -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

if [ -z "$USER_ID" ]; then
    USER_ID=$(curl -sf -X POST "$GATEWAY_URL/admin/users" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"email":"test@vexa.ai","name":"Test User"}' 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
fi

API_TOKEN=""
if [ -n "$USER_ID" ]; then
    API_TOKEN=$(curl -sf -X POST "$GATEWAY_URL/admin/users/$USER_ID/tokens?scopes=bot,browser,tx&name=tests3" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")
    if [ -n "$API_TOKEN" ]; then
        pass "API token: created for test@vexa.ai"
    else
        fail "could not create API token"
    fi
fi

# ── 7. Helm upgrade (phase 2 — inject VEXA_API_KEY) ─
if [ -n "$API_TOKEN" ]; then
    info "upgrading chart with VEXA_API_KEY..."
    HELM_ARGS+=(--set "secrets.vexaApiKey=$API_TOKEN")
    helm upgrade vexa "$CHART_PATH" "${HELM_ARGS[@]}" --wait --timeout 5m 2>&1 | tail -3
    pass "dashboard configured with API key"
fi

# ── 9. Write state for tests ────────────────────
state_write deploy_mode "helm"
state_write gateway_url "$GATEWAY_URL"
state_write admin_url "$ADMIN_URL"
state_write dashboard_url "$DASHBOARD_URL"
state_write admin_token "$ADMIN_TOKEN"
[ -n "$API_TOKEN" ] && state_write api_token "$API_TOKEN"

# Detect helm release
HELM_RELEASE=$(kubectl get deploy -l app.kubernetes.io/name=vexa \
    -o jsonpath='{.items[0].metadata.labels.app\.kubernetes\.io/instance}' 2>/dev/null || echo "")
if [ -n "$HELM_RELEASE" ]; then
    state_write helm_release "$HELM_RELEASE"
    pass "release: $HELM_RELEASE"
fi

state_write lke_setup complete

echo "  ──────────────────────────────────────────────"
echo ""
echo "  Gateway:   $GATEWAY_URL"
echo "  Dashboard: $DASHBOARD_URL"
echo "  kubectl:   export KUBECONFIG=$KUBECONFIG"
echo ""
