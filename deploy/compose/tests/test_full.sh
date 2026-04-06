#!/usr/bin/env bash
set -euo pipefail

# Full Docker Compose deployment test
# Usage: cd deploy/compose && ./tests/test_full.sh

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

PASS=0
FAIL=0
RESULTS=""

check() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    RESULTS+="PASS: $name\n"
    ((PASS++))
  else
    RESULTS+="FAIL: $name\n"
    ((FAIL++))
  fi
}

echo "=== Docker Compose Full Test ==="
echo "Started: $(date -Iseconds)"
echo ""

# Step 1: Clean start
echo ">>> Stopping existing stack..."
make down 2>/dev/null || true
rm -f .env

# Step 2: Build and start
echo ">>> Running make all..."
if make all; then
  RESULTS+="PASS: make all\n"
  ((PASS++))
else
  RESULTS+="FAIL: make all\n"
  ((FAIL++))
  echo -e "\n=== RESULTS ===\n$RESULTS"
  echo "PASS: $PASS  FAIL: $FAIL"
  exit 1
fi

echo ">>> Waiting for services to stabilize..."
sleep 15

# Step 3: Check containers
echo ">>> Checking containers..."
check "all containers running" make ps

# Step 4: Check endpoints
echo ">>> Checking endpoints..."
check "API Gateway :8056" curl -sf http://localhost:8056/docs
check "Admin API :8057" curl -sf http://localhost:8057/docs
check "Dashboard :3001" curl -sf http://localhost:3001

# Step 5: Check database
echo ">>> Checking database..."
check "PostgreSQL responding" docker compose exec -T postgres pg_isready -U postgres
check "Schema sync" docker compose exec -T meeting-api python -c "from meeting_api.database import init_db; print('ok')"

# Step 6: Check Redis
echo ">>> Checking Redis..."
check "Redis ping" docker compose exec -T redis redis-cli ping

# Step 7: Check logs for errors
echo ">>> Checking logs for fatal errors..."
ERROR_COUNT=$(make logs 2>&1 | grep -ic "fatal\|panic\|cannot start" || true)
if [ "$ERROR_COUNT" -eq 0 ]; then
  RESULTS+="PASS: no fatal errors in logs\n"
  ((PASS++))
else
  RESULTS+="FAIL: $ERROR_COUNT fatal errors in logs\n"
  ((FAIL++))
fi

# Report
echo ""
echo "=== RESULTS ==="
echo -e "$RESULTS"
echo "PASS: $PASS  FAIL: $FAIL"
echo "Finished: $(date -Iseconds)"

# Save results
mkdir -p tests/results
echo -e "$(date -Iseconds)\n\n$RESULTS\nPASS: $PASS  FAIL: $FAIL" > tests/results/last_run.txt

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
