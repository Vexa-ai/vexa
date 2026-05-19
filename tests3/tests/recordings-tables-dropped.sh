#!/usr/bin/env bash
# recordings-tables-dropped — RECORDINGS_TABLE_DROPPED_IN_PROD
#
# Proves: the `recordings` and `media_files` relational tables are
# absent from the live database (compose-local or helm prod). v0.10.6.1
# moved recording metadata fully into `meetings.data['recordings']`
# JSONB; the relational tables were dropped by m331.
#
# Mode: compose, helm.
#
# Steps:
#   1. Query information_schema.tables for `recordings`. Must be empty.
#   2. Query information_schema.tables for `media_files`. Must be empty.
#
# Why both checks: m331 drops media_files first (FK-safe order). A
# partial run that dropped media_files but not recordings would
# violate the invariant — this script catches that.
#
# Idempotent: read-only.

set -euo pipefail

usage() {
    cat <<EOF
Usage: $0
Proves RECORDINGS_TABLE_DROPPED_IN_PROD.
Reads from vexa-postgres-1 (compose) or PG_CONTAINER env override (helm).
EOF
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=../lib/common.sh
source "$ROOT_DIR/lib/common.sh"

test_begin recordings-tables-dropped

PG_CONTAINER="${PG_CONTAINER:-vexa-postgres-1}"
PG_USER="${POSTGRES_USER:-postgres}"
PG_DB="${POSTGRES_DB:-vexa}"

if ! command -v docker >/dev/null 2>&1; then
    step_fail RECORDINGS_TABLE_DROPPED_IN_PROD "docker not available — required to reach $PG_CONTAINER"
    exit 1
fi

if ! docker exec "$PG_CONTAINER" true >/dev/null 2>&1; then
    step_fail RECORDINGS_TABLE_DROPPED_IN_PROD "cannot exec into $PG_CONTAINER"
    exit 1
fi

check_table_absent() {
    local t="$1"
    local out
    out=$(docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -A -t -c \
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$t'" \
        2>/dev/null) || {
        step_fail RECORDINGS_TABLE_DROPPED_IN_PROD "psql query failed for table $t"
        return 1
    }
    out=$(echo "$out" | tr -d '[:space:]')
    if [ -n "$out" ]; then
        step_fail RECORDINGS_TABLE_DROPPED_IN_PROD \
            "table '$t' still present in database (expected dropped by m331)"
        return 1
    fi
    return 0
}

if ! check_table_absent "recordings"; then
    exit 1
fi
if ! check_table_absent "media_files"; then
    exit 1
fi

step_pass RECORDINGS_TABLE_DROPPED_IN_PROD \
    "neither 'recordings' nor 'media_files' present in $PG_DB"
