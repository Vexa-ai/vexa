#!/usr/bin/env bash
# Load a production database dump into the LKE cluster's Postgres.
# Usage: lke-load-db.sh [dump_file]
# Reads: .state/lke_kubeconfig_path
# Default dump: /home/dima/dev/2/secrets/production-dump.sql
set -euo pipefail
source "$(dirname "$0")/common.sh"

export KUBECONFIG=$(state_read lke_kubeconfig_path)
DUMP_FILE="${1:-/home/dima/dev/2/secrets/production-dump.sql}"

if [ ! -f "$DUMP_FILE" ]; then
    fail "dump file not found: $DUMP_FILE"
    exit 1
fi

echo ""
echo "  lke-load-db"
echo "  ──────────────────────────────────────────────"

DUMP_SIZE=$(du -sh "$DUMP_FILE" | cut -f1)
info "dump: $DUMP_FILE ($DUMP_SIZE)"

# ── 1. Get Postgres credentials from the cluster ──
PG_POD=$(kubectl get pods -l app.kubernetes.io/component=postgres --no-headers | awk '{print $1}' | head -1)
if [ -z "$PG_POD" ]; then
    fail "no postgres pod found"
    exit 1
fi

PG_USER=$(kubectl exec "$PG_POD" -- printenv POSTGRES_USER 2>/dev/null || echo "postgres")
PG_DB=$(kubectl exec "$PG_POD" -- printenv POSTGRES_DB 2>/dev/null || echo "vexa")
pass "postgres: $PG_POD (db=$PG_DB user=$PG_USER)"

# ── 2. Drop and recreate the database ─────────────
info "resetting database..."
kubectl exec "$PG_POD" -- psql -U "$PG_USER" -c "
    SELECT pg_terminate_backend(pid) FROM pg_stat_activity
    WHERE datname = '$PG_DB' AND pid <> pg_backend_pid();
" > /dev/null 2>&1 || true

kubectl exec "$PG_POD" -- psql -U "$PG_USER" -c "DROP DATABASE IF EXISTS $PG_DB;" > /dev/null 2>&1
kubectl exec "$PG_POD" -- psql -U "$PG_USER" -c "CREATE DATABASE $PG_DB;" > /dev/null 2>&1
pass "database reset: $PG_DB"

# ── 3. Load the dump ──────────────────────────────
info "loading dump (this may take a minute)..."

# Stream the dump into psql via kubectl exec
# Filter out Supabase-specific commands that fail on vanilla Postgres
sed '/^\\restrict/d; /^\\set/d; /^ALTER.*OWNER TO "supabase/d; /^GRANT.*supabase/d; /^REVOKE.*supabase/d' "$DUMP_FILE" | \
    kubectl exec -i "$PG_POD" -- psql -U "$PG_USER" -d "$PG_DB" -q 2>&1 | \
    grep -v "^$\|^SET\|^COMMENT\|already exists\|does not exist" | tail -5

pass "dump loaded"

# ── 4. Verify row counts ─────────────────────────
info "verifying data..."
COUNTS=$(kubectl exec "$PG_POD" -- psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT 'users=' || count(*) FROM users
    UNION ALL SELECT 'meetings=' || count(*) FROM meetings
    UNION ALL SELECT 'transcriptions=' || count(*) FROM transcriptions
    UNION ALL SELECT 'api_tokens=' || count(*) FROM api_tokens;
" 2>/dev/null | tr -d ' ' | grep -v '^$')
echo "$COUNTS" | while read line; do
    info "$line"
done

TOTAL_USERS=$(echo "$COUNTS" | grep users= | cut -d= -f2)
if [ "${TOTAL_USERS:-0}" -gt 0 ]; then
    pass "data verified: $TOTAL_USERS users"
else
    fail "no users found after load"
    exit 1
fi

# ── 5. Restart services to pick up new data ───────
info "restarting services..."
kubectl rollout restart deploy/vexa-vexa-admin-api deploy/vexa-vexa-meeting-api deploy/vexa-vexa-api-gateway > /dev/null 2>&1
kubectl rollout status deploy/vexa-vexa-admin-api --timeout=120s > /dev/null 2>&1
kubectl rollout status deploy/vexa-vexa-meeting-api --timeout=120s > /dev/null 2>&1
kubectl rollout status deploy/vexa-vexa-api-gateway --timeout=120s > /dev/null 2>&1
pass "services restarted"

# ── 6. Re-bootstrap credentials ──────────────────
info "re-bootstrapping credentials..."
rm -f "$STATE/admin_token" "$STATE/api_token"
NODE_IP=$(state_read lke_node_ip)
GATEWAY_URL=$(state_read gateway_url)
ADMIN_TOKEN=$(kubectl exec deploy/vexa-vexa-admin-api -- printenv ADMIN_API_TOKEN 2>/dev/null)

USER_ID=$(curl -sf "$GATEWAY_URL/admin/users/email/test@vexa.ai" \
    -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
if [ -z "$USER_ID" ]; then
    # max_concurrent_bots=3 — match lke-setup-helm.sh; helm cluster supports
    # concurrent multi-bot validation. Default admin-api applies 0 (DB) →
    # gateway falls back to 1, silently rate-limiting multi-bot scenarios.
    USER_ID=$(curl -sf -X POST "$GATEWAY_URL/admin/users" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" -H "Content-Type: application/json" \
        -d '{"email":"test@vexa.ai","name":"Test User","max_concurrent_bots":3}' 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
fi
# Ensure existing user (re-load case) also has the right limit.
if [ -n "$USER_ID" ]; then
    curl -sf -X PATCH "$GATEWAY_URL/admin/users/$USER_ID" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" -H "Content-Type: application/json" \
        -d '{"max_concurrent_bots":3}' 2>/dev/null >/dev/null || true
fi

API_TOKEN=""
if [ -n "$USER_ID" ]; then
    API_TOKEN=$(curl -sf -X POST "$GATEWAY_URL/admin/users/$USER_ID/tokens?scopes=bot,browser,tx&name=tests3-proddb" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")
fi

state_write admin_token "$ADMIN_TOKEN"
[ -n "$API_TOKEN" ] && state_write api_token "$API_TOKEN"

# Upgrade helm with new VEXA_API_KEY for dashboard
if [ -n "$API_TOKEN" ]; then
    CHART_PATH="$ROOT/deploy/helm/charts/vexa"
    TX_URL=$(grep -E '^TRANSCRIPTION_SERVICE_URL=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || echo "")
    TX_TOKEN=$(grep -E '^TRANSCRIPTION_SERVICE_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || echo "")
    IMAGE_TAG=$(grep -E '^IMAGE_TAG=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || echo "")
    HELM_ARGS=(
        --values "$CHART_PATH/values-test.yaml"
        --set "dashboard.env.VEXA_PUBLIC_API_URL=$GATEWAY_URL"
        --set "dashboard.env.NEXT_PUBLIC_APP_URL=$(state_read dashboard_url)"
        --set "secrets.vexaApiKey=$API_TOKEN"
    )
    [ -n "$IMAGE_TAG" ] && HELM_ARGS+=(--set "global.imageTag=$IMAGE_TAG")
    [ -n "$TX_URL" ] && HELM_ARGS+=(--set "meetingApi.transcriptionServiceUrl=$TX_URL" --set "meetingApi.transcriptionServiceToken=$TX_TOKEN")
    helm upgrade vexa "$CHART_PATH" "${HELM_ARGS[@]}" --wait --timeout 5m > /dev/null 2>&1
    kubectl rollout restart deploy/vexa-vexa-dashboard > /dev/null 2>&1
    kubectl rollout status deploy/vexa-vexa-dashboard --timeout=120s > /dev/null 2>&1
    pass "credentials re-bootstrapped + dashboard restarted"
else
    fail "could not create API token"
fi

echo "  ──────────────────────────────────────────────"
echo ""
