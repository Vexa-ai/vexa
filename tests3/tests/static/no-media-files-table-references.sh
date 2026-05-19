#!/usr/bin/env bash
# no-media-files-table-references — MEDIA_FILES_TABLE_NOT_REFERENCED
#
# Proves: no live code path references the dropped relational
# `MediaFile` ORM model. v0.10.6.1 removed the model; the
# `media_files` table is dropped by m331. Any remaining
# `select(MediaFile)`, `MediaFile(...)` constructor, or
# `from … import … MediaFile` is dead code that crashes at import.
#
# Mode: any (static grep over services/).
#
# Allow-list:
#   - The JSONB key `media_files` (list-of-dicts on each recording) is
#     a separate concept and intentionally retained — only the Python
#     ORM class reference is forbidden. This script checks code forms,
#     not the JSONB key.
#
# Steps:
#   1. zero `select(MediaFile)` under services/.
#   2. zero `from … models import …, MediaFile` under services/.
#   3. zero `MediaFile(` constructor calls under
#      services/meeting-api/meeting_api/.
#
# Idempotent: read-only.

set -euo pipefail

usage() {
    cat <<EOF
Usage: $0
Proves MEDIA_FILES_TABLE_NOT_REFERENCED via static grep.
EOF
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck source=../../lib/common.sh
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin no-media-files-table-references

SERVICES_DIR="$ROOT_DIR/services"
if [ ! -d "$SERVICES_DIR" ]; then
    step_fail MEDIA_FILES_TABLE_NOT_REFERENCED "services/ not found at $SERVICES_DIR"
    exit 1
fi

HITS1=$(grep -rn "select(MediaFile)" "$SERVICES_DIR" --include='*.py' || true)
if [ -n "$HITS1" ]; then
    step_fail MEDIA_FILES_TABLE_NOT_REFERENCED \
        "select(MediaFile) still referenced: $(echo "$HITS1" | head -3 | tr '\n' '|')"
    exit 1
fi

HITS2=$(grep -rnE "from .{0,200}models import.{0,200}MediaFile\b" "$SERVICES_DIR" --include='*.py' || true)
if [ -n "$HITS2" ]; then
    step_fail MEDIA_FILES_TABLE_NOT_REFERENCED \
        "ORM import of MediaFile still present: $(echo "$HITS2" | head -3 | tr '\n' '|')"
    exit 1
fi

HITS3=$(grep -rnE "\bMediaFile\(" "$SERVICES_DIR/meeting-api/meeting_api/" --include='*.py' || true)
if [ -n "$HITS3" ]; then
    step_fail MEDIA_FILES_TABLE_NOT_REFERENCED \
        "MediaFile(...) constructor still called: $(echo "$HITS3" | head -3 | tr '\n' '|')"
    exit 1
fi

step_pass MEDIA_FILES_TABLE_NOT_REFERENCED \
    "no select(MediaFile), no ORM import, no MediaFile() constructor in services/"
