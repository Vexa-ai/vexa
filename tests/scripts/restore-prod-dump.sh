#!/usr/bin/env bash
# Restore a Supabase production dump into the local compose postgres.
#
# The production dump comes from Supabase (Postgres 17) and contains schemas
# and extensions that don't exist in our local postgres:16-alpine. This script
# extracts only the public schema (our application tables + data), loads it,
# then runs ensure_schema() to add any columns the current code expects but
# the dump doesn't have.
#
# Usage:
#   tests/scripts/restore-prod-dump.sh <path-to-dump.sql>
#   make restore-db DUMP=~/secrets/production-dump.sql
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ROOT/.env"
COMPOSE_FILE="$ROOT/deploy/compose/docker-compose.yml"
COMPOSE_CMD="docker compose --env-file $ENV_FILE -f $COMPOSE_FILE"

DUMP="${1:?Usage: $0 <path-to-dump.sql>}"

if [ ! -f "$DUMP" ]; then
  echo "ERROR: Dump file not found: $DUMP"
  exit 1
fi

# Read DB config from .env (same defaults as docker-compose.yml)
DB_USER=$(grep -E '^DB_USER=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "postgres")
DB_NAME=$(grep -E '^DB_NAME=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "vexa")
DB_USER=${DB_USER:-postgres}
DB_NAME=${DB_NAME:-vexa}

echo "=== Production dump restore ==="
echo "Dump:     $DUMP"
echo "Database: $DB_NAME (user: $DB_USER)"
echo "Lines:    $(wc -l < "$DUMP")"
echo ""

# -------------------------------------------------------------------------
# 1. Verify postgres is running
# -------------------------------------------------------------------------
if ! $COMPOSE_CMD ps -q postgres | grep -q .; then
  echo "ERROR: postgres not running. Run 'make up' first (just postgres is enough)."
  echo "  docker compose --env-file .env -f deploy/compose/docker-compose.yml up -d postgres"
  exit 1
fi

echo "Waiting for postgres to be ready..."
count=0
while ! $COMPOSE_CMD exec -T postgres pg_isready -U "$DB_USER" -d "$DB_NAME" -q 2>/dev/null; do
  if [ $count -ge 12 ]; then echo "ERROR: DB not ready after 60s."; exit 1; fi
  sleep 5; count=$((count+1))
done
echo "Postgres is ready."
echo ""

# -------------------------------------------------------------------------
# 2. Extract public-schema-only SQL from the Supabase dump
# -------------------------------------------------------------------------
echo "Extracting public schema from Supabase dump..."
CLEAN_DUMP=$(mktemp /tmp/vexa-public-restore-XXXXXX.sql)
trap 'rm -f "$CLEAN_DUMP"' EXIT

{
  # Preamble: SET statements (lines before first CREATE SCHEMA)
  echo "-- Cleaned public-schema-only restore from production dump"
  echo "-- Generated: $(date -Iseconds)"
  echo ""
  echo "SET statement_timeout = 0;"
  echo "SET lock_timeout = 0;"
  echo "SET client_encoding = 'UTF8';"
  echo "SET standard_conforming_strings = on;"
  echo "SELECT pg_catalog.set_config('search_path', '', false);"
  echo "SET check_function_bodies = false;"
  echo "SET client_min_messages = warning;"
  echo "SET row_security = off;"
  echo ""

  # Extract only public schema objects.
  # Strategy: use awk to grab blocks tagged with "Schema: public"
  # plus COPY blocks for public.* tables, plus setval for public.* sequences.
  #
  # The dump has two header formats:
  #   -- Name: <obj>; Type: <type>; Schema: <schema>; Owner: -
  #   -- Data for Name: <obj>; Type: TABLE DATA; Schema: <schema>; Owner: -
  # We must terminate block mode on EITHER format when schema != public.
  awk '
    # Track if we are inside a COPY public.* data block
    /^COPY public\./ { in_copy=1; print; next }
    in_copy && /^\\.$/ { print; in_copy=0; next }
    in_copy { print; next }

    # Skip COPY blocks for non-public schemas entirely
    /^COPY [a-z]/ && !/^COPY public\./ { in_skip=1; next }
    in_skip && /^\\.$/ { in_skip=0; next }
    in_skip { next }

    # Enter block mode for public schema headers (both Name: and Data for Name:)
    /^-- (Data for )?Name:.*Schema: public/ { in_block=1; print; next }

    # Exit block mode on non-public headers (both formats)
    in_block && /^-- (Data for )?Name:/ && !/Schema: public/ { in_block=0; blank_count=0; next }

    # Inside a block: pass through content (with blank-line limiting)
    in_block && /^$/ { blank_count++; if (blank_count <= 2) print; next }
    in_block && /^--$/ { blank_count=0; print; next }
    in_block { blank_count=0; print; next }

    # Standalone lines outside blocks: grab public-schema DDL
    /^ALTER TABLE ONLY public\./ { print; next }
    /^CREATE.*INDEX.*ON public\./ { print; next }
    /^ALTER SEQUENCE public\./ { print; next }

    # Sequence setval calls
    /^SELECT pg_catalog\.setval\(.*public\./ { print; next }

    # DEFAULT nextval lines (needed before COPY for serial columns)
    /nextval\(.*public\./ { print; next }
  ' "$DUMP"

} > "$CLEAN_DUMP"

CLEAN_LINES=$(wc -l < "$CLEAN_DUMP")
echo "Extracted $CLEAN_LINES lines (from $(wc -l < "$DUMP") total)."
echo ""

# -------------------------------------------------------------------------
# 3. Drop existing public tables (if any) to get a clean restore
# -------------------------------------------------------------------------
echo "Dropping existing public schema tables..."
$COMPOSE_CMD exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -q <<'SQL'
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
    EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
  END LOOP;
  FOR r IN (SELECT sequencename FROM pg_sequences WHERE schemaname = 'public') LOOP
    EXECUTE 'DROP SEQUENCE IF EXISTS public.' || quote_ident(r.sequencename) || ' CASCADE';
  END LOOP;
END $$;
SQL
echo "Done."
echo ""

# -------------------------------------------------------------------------
# 4. Restore the cleaned dump
# -------------------------------------------------------------------------
echo "Restoring public schema from dump..."
# Use -v ON_ERROR_STOP=0 to continue past minor errors (e.g. duplicate objects)
$COMPOSE_CMD exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -q \
  --set ON_ERROR_STOP=0 < "$CLEAN_DUMP" 2>&1 | \
  grep -v "^SET$" | grep -v "^$" | head -30 || true
echo ""
echo "Restore complete."
echo ""

# -------------------------------------------------------------------------
# 5. Verify row counts
# -------------------------------------------------------------------------
echo "=== Row counts ==="
$COMPOSE_CMD exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -t -c "
  SELECT relname AS table, n_live_tup AS rows
  FROM pg_stat_user_tables
  WHERE schemaname = 'public'
  ORDER BY relname;
"

echo ""
echo "Running ANALYZE to update statistics..."
$COMPOSE_CMD exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -q -c "ANALYZE;"
echo ""

# -------------------------------------------------------------------------
# 6. Run schema sync to add any new columns the current code expects
# -------------------------------------------------------------------------
echo "Running schema sync (adds missing columns from current code)..."
echo "  The dump may be older than the current code. ensure_schema() will:"
echo "  - Add api_tokens.scopes, api_tokens.name, api_tokens.last_used_at, api_tokens.expires_at"
echo "  - Add transcriptions.segment_id"
echo "  - Add any other new columns"
echo ""

# Schema sync needs both admin-api (for admin models) and meeting-api (for meeting models).
# The meeting-api init_db only syncs meeting-api columns — admin tables are prerequisites
# and only get create_all(checkfirst=True), which doesn't add new columns.
SYNCED=0
if $COMPOSE_CMD ps -q admin-api 2>/dev/null | grep -q .; then
  echo "  Syncing admin models (api_tokens, users)..."
  $COMPOSE_CMD exec -T admin-api python -c \
    "import asyncio; from admin_models.database import init_db; asyncio.run(init_db())"
  SYNCED=1
fi
if $COMPOSE_CMD ps -q meeting-api 2>/dev/null | grep -q .; then
  echo "  Syncing meeting models (meetings, transcriptions, etc.)..."
  $COMPOSE_CMD exec -T meeting-api python -c \
    "import asyncio; from meeting_api.database import init_db; asyncio.run(init_db())"
  SYNCED=1
fi
if [ "$SYNCED" -eq 1 ]; then
  echo "Schema sync complete."
else
  echo "WARNING: neither admin-api nor meeting-api is running."
  echo "  Run 'make up && make init-db' after starting services."
fi

echo ""
echo "=== Restore finished ==="
echo ""
echo "Next steps:"
echo "  1. Start all services:  make up"
echo "  2. Schema sync:         make init-db    (if not already run above)"
echo "  3. Verify:              make test"
echo "  4. Dashboard:           http://localhost:\${DASHBOARD_HOST_PORT:-3001}"
