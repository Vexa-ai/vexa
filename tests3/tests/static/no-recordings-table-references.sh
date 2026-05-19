#!/usr/bin/env bash
# no-recordings-table-references — RECORDINGS_TABLE_NOT_REFERENCED
#
# Proves: no live code path references the dropped relational
# `Recording` ORM model. v0.10.6.1 removed the model + the
# RECORDING_METADATA_MODE toggle; the `recordings` table is dropped
# by m331. Any remaining `select(Recording)`, `Recording(...)`
# constructor, or `from … import … Recording` is dead code that will
# crash at import-time on a v0.10.6.1+ deploy.
#
# Mode: any (static grep over services/).
#
# Allow-list:
#   - The `Recording` keyword in comments, docstrings, or user-facing
#     strings is allowed (e.g. response body keys, schema docstrings).
#   - This script intentionally checks code-level Python forms only.
#
# Steps:
#   1. zero `select(Recording)` calls anywhere under services/.
#   2. zero `from <anything> import … Recording` lines anywhere under
#      services/ (Meeting and MeetingSession remain).
#   3. zero `Recording(` constructor calls under services/meeting-api/
#      meeting_api/.
#
# Idempotent: read-only.

set -euo pipefail

usage() {
    cat <<EOF
Usage: $0
Proves RECORDINGS_TABLE_NOT_REFERENCED via static grep.
EOF
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck source=../../lib/common.sh
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin no-recordings-table-references

SERVICES_DIR="$ROOT_DIR/services"
if [ ! -d "$SERVICES_DIR" ]; then
    step_fail RECORDINGS_TABLE_NOT_REFERENCED "services/ not found at $SERVICES_DIR"
    exit 1
fi

# Step 1
HITS1=$(grep -rn "select(Recording)" "$SERVICES_DIR" --include='*.py' || true)
if [ -n "$HITS1" ]; then
    step_fail RECORDINGS_TABLE_NOT_REFERENCED \
        "select(Recording) still referenced: $(echo "$HITS1" | head -3 | tr '\n' '|')"
    exit 1
fi

# Step 2: `from … models import …, Recording, …`. Use grep -E with
# a bounded gap so we don't false-positive on multi-line imports
# that happen to include the literal Recording elsewhere.
HITS2=$(grep -rnE "from .{0,200}models import.{0,200}Recording\b" "$SERVICES_DIR" --include='*.py' || true)
if [ -n "$HITS2" ]; then
    step_fail RECORDINGS_TABLE_NOT_REFERENCED \
        "ORM import of Recording still present: $(echo "$HITS2" | head -3 | tr '\n' '|')"
    exit 1
fi

# Step 3: `Recording(` constructor under meeting-api code.
HITS3=$(grep -rnE "\bRecording\(" "$SERVICES_DIR/meeting-api/meeting_api/" --include='*.py' || true)
if [ -n "$HITS3" ]; then
    step_fail RECORDINGS_TABLE_NOT_REFERENCED \
        "Recording(...) constructor still called: $(echo "$HITS3" | head -3 | tr '\n' '|')"
    exit 1
fi

step_pass RECORDINGS_TABLE_NOT_REFERENCED \
    "no select(Recording), no ORM import, no Recording() constructor in services/"
