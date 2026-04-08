#!/usr/bin/env bash
# Restore a production database dump into the local compose postgres.
# Usage: restore-prod-dump.sh <path-to-dump.sql>
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
ENV_FILE="$ROOT/.env"

DUMP_FILE="${1:?Usage: restore-prod-dump.sh <path-to-dump.sql>}"

if [ ! -f "$DUMP_FILE" ]; then
    echo "ERROR: dump file not found: $DUMP_FILE"
    exit 1
fi

COMPOSE_FILE="$ROOT/deploy/compose/docker-compose.yml"
COMPOSE_CMD="docker compose --env-file $ENV_FILE -f $COMPOSE_FILE"

DB_USER=$(grep -E '^DB_USER=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "postgres")
DB_NAME=$(grep -E '^DB_NAME=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "vexa")
DB_USER=${DB_USER:-postgres}
DB_NAME=${DB_NAME:-vexa}

DUMP_SIZE=$(du -sh "$DUMP_FILE" | cut -f1)
echo ""
echo "  restore-prod-dump (compose)"
echo "  ──────────────────────────────────────────────"
echo "  dump: $DUMP_FILE ($DUMP_SIZE)"
echo "  db:   $DB_NAME  user: $DB_USER"
echo ""

# 1. Ensure postgres is running
if ! $COMPOSE_CMD ps -q postgres | grep -q .; then
    echo "ERROR: postgres not running. Run 'make -C deploy/compose up' first."
    exit 1
fi
echo "  [+] postgres is running"

# 2. Wait for postgres to be ready
count=0
while ! $COMPOSE_CMD exec -T postgres pg_isready -U "$DB_USER" -d "$DB_NAME" -q 2>/dev/null; do
    if [ $count -ge 12 ]; then echo "ERROR: DB not ready in 60s."; exit 1; fi
    sleep 5; count=$((count+1))
done
echo "  [+] postgres is ready"

# 3. Drop and recreate the database
echo "  [~] resetting database..."
$COMPOSE_CMD exec -T postgres psql -U "$DB_USER" -c "
    SELECT pg_terminate_backend(pid) FROM pg_stat_activity
    WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();
" > /dev/null 2>&1 || true

$COMPOSE_CMD exec -T postgres psql -U "$DB_USER" -c "DROP DATABASE IF EXISTS $DB_NAME;" > /dev/null 2>&1
$COMPOSE_CMD exec -T postgres psql -U "$DB_USER" -c "CREATE DATABASE $DB_NAME;" > /dev/null 2>&1
echo "  [+] database reset: $DB_NAME"

# 4. Load the dump
echo "  [~] loading dump (this may take a minute)..."
# Filter out Supabase-specific commands that fail on vanilla Postgres
sed '/^\\restrict/d; /^\\set/d; /^ALTER.*OWNER TO "supabase/d; /^GRANT.*supabase/d; /^REVOKE.*supabase/d' "$DUMP_FILE" | \
    $COMPOSE_CMD exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -q 2>&1 | \
    grep -v "^$\|^SET\|^COMMENT\|already exists\|does not exist" | tail -5
echo "  [+] dump loaded"

# 5. Verify row counts
echo "  [~] verifying data..."
COUNTS=$($COMPOSE_CMD exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT 'users=' || count(*) FROM users
    UNION ALL SELECT 'meetings=' || count(*) FROM meetings
    UNION ALL SELECT 'transcriptions=' || count(*) FROM transcriptions
    UNION ALL SELECT 'api_tokens=' || count(*) FROM api_tokens;
" 2>/dev/null | tr -d ' ' | grep -v '^$')
echo "$COUNTS" | while read line; do
    echo "      $line"
done

TOTAL_USERS=$(echo "$COUNTS" | grep users= | cut -d= -f2)
if [ "${TOTAL_USERS:-0}" -gt 0 ]; then
    echo "  [+] data verified: $TOTAL_USERS users"
else
    echo "  [-] no users found after load"
    exit 1
fi

# 6. Run schema sync via meeting-api
echo "  [~] running schema sync..."
$COMPOSE_CMD exec -T meeting-api python -c "import asyncio; from admin_models.database import init_db; asyncio.run(init_db())" 2>&1 || true
$COMPOSE_CMD exec -T meeting-api python -c "import asyncio; from meeting_api.database import init_db; asyncio.run(init_db())" 2>&1 || true
echo "  [+] schema sync complete"

# 7. Restart services to pick up new data
echo "  [~] restarting services..."
$COMPOSE_CMD restart admin-api meeting-api api-gateway > /dev/null 2>&1

# Wait for services to be healthy
sleep 10
count=0
while ! curl -sf http://localhost:8056/docs > /dev/null 2>&1; do
    if [ $count -ge 24 ]; then echo "  [-] services not healthy after 120s"; exit 1; fi
    sleep 5; count=$((count+1))
done
echo "  [+] services restarted and healthy"

echo "  ──────────────────────────────────────────────"
echo ""
