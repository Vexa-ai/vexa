#!/usr/bin/env bash
#
# Smoke test: verify all services are healthy and basic operations work.
#
# Usage:
#   bash tests/smoke/test_full_stack.sh
#
# Prerequisites:
#   - All services running via docker compose up -d
#   - .env configured

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Load env vars
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

API_PORT="${API_GATEWAY_HOST_PORT:-8056}"
ADMIN_PORT="${ADMIN_API_HOST_PORT:-8057}"
API_BASE="http://localhost:$API_PORT"
ADMIN_BASE="http://localhost:$ADMIN_PORT"

PASS=0
FAIL=0
SKIP=0

check() {
    local name="$1"
    local cmd="$2"

    printf "  %-50s" "$name"
    if eval "$cmd" > /dev/null 2>&1; then
        echo "PASS"
        PASS=$((PASS + 1))
    else
        echo "FAIL"
        FAIL=$((FAIL + 1))
    fi
}

skip() {
    local name="$1"
    local reason="$2"
    printf "  %-50s" "$name"
    echo "SKIP ($reason)"
    SKIP=$((SKIP + 1))
}

echo "============================================"
echo "  Vexa Smoke Test"
echo "  $(date)"
echo "============================================"
echo ""

# --- Service Health ---
echo "[Service Health]"
check "API Gateway responds" \
    "curl -sf '$API_BASE/docs' -o /dev/null --max-time 10"

check "Admin API responds" \
    "curl -sf '$ADMIN_BASE/docs' -o /dev/null --max-time 10"

check "API Gateway OpenAPI spec" \
    "curl -sf '$API_BASE/openapi.json' -o /dev/null --max-time 10"

check "Admin API OpenAPI spec" \
    "curl -sf '$ADMIN_BASE/openapi.json' -o /dev/null --max-time 10"

echo ""

# --- Docker Services ---
echo "[Docker Services]"
check "Docker is running" \
    "docker info > /dev/null 2>&1"

check "Containers are up" \
    "docker compose -f '$REPO_ROOT/docker-compose.yml' ps --format '{{.Status}}' | grep -q 'Up'"

echo ""

# --- API Basic Operations ---
echo "[API Operations]"

# Test unauthenticated access is rejected
check "API Gateway rejects unauthenticated request" \
    "test \$(curl -so /dev/null -w '%{http_code}' '$API_BASE/meetings' --max-time 10) -eq 401 -o \$(curl -so /dev/null -w '%{http_code}' '$API_BASE/meetings' --max-time 10) -eq 403"

# Test admin API with token
if [ -n "${ADMIN_API_TOKEN:-}" ]; then
    check "Admin API accepts valid token" \
        "curl -sf '$ADMIN_BASE/admin/users' -H 'X-Admin-API-Key: $ADMIN_API_TOKEN' --max-time 10 -o /dev/null"
else
    skip "Admin API token auth" "ADMIN_API_TOKEN not set"
fi

echo ""

# --- Database ---
echo "[Database]"
REMOTE_DB="${REMOTE_DB:-false}"
if [ "$REMOTE_DB" != "true" ]; then
    check "PostgreSQL container running" \
        "docker compose -f '$REPO_ROOT/docker-compose.yml' -f '$REPO_ROOT/docker-compose.local-db.yml' ps postgres 2>/dev/null | grep -q 'Up'"

    check "PostgreSQL accepting connections" \
        "docker compose -f '$REPO_ROOT/docker-compose.yml' -f '$REPO_ROOT/docker-compose.local-db.yml' exec -T postgres pg_isready -U postgres 2>/dev/null"
else
    skip "Local PostgreSQL" "REMOTE_DB=true"
fi

echo ""

# --- Summary ---
TOTAL=$((PASS + FAIL + SKIP))
echo "============================================"
echo "  Results: $PASS passed, $FAIL failed, $SKIP skipped (total: $TOTAL)"
echo "============================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
