#!/usr/bin/env bash
# dashboard-playback-canonical — DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES
#
# Proves: the dashboard reads `recording.playback_url.{audio,video}`
# as the canonical pointer to the master media file, and does NOT do
# client-side picking of the master from `media_files[]` via
# `pickMasterMediaFile(...)` or `media_files.find(...)` patterns.
#
# Mode: any (static-grep against services/dashboard/).
#
# Why: v0.10.6.1 ADR-2. Picking the master client-side was buggy and
# diverged between pages; the backend now owns the canonical URL.
#
# Steps:
#   1. zero references to pickMasterMediaFile in services/dashboard/.
#   2. zero references to media_files .find() patterns under
#      services/dashboard/src/.
#   3. at least one reference to playback_url under
#      services/dashboard/src/ (confirms positive read of the new field).
#
# Idempotent: read-only.

set -euo pipefail

usage() {
    cat <<EOF
Usage: $0
Proves DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES via static greps.
Run from any working directory; resolves paths relative to repo root.
EOF
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck source=../../lib/common.sh
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin dashboard-playback-canonical

DASH_DIR="$ROOT_DIR/services/dashboard"
DASH_SRC="$DASH_DIR/src"

if [ ! -d "$DASH_DIR" ]; then
    step_fail DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES \
        "services/dashboard not found at $DASH_DIR"
    exit 1
fi
if [ ! -d "$DASH_SRC" ]; then
    step_fail DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES \
        "services/dashboard/src not found at $DASH_SRC"
    exit 1
fi

# Step 1: pickMasterMediaFile must be gone.
PICK_HITS=$(grep -rn "pickMasterMediaFile" "$DASH_DIR" || true)
if [ -n "$PICK_HITS" ]; then
    step_fail DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES \
        "pickMasterMediaFile still referenced: $(echo "$PICK_HITS" | head -3 | tr '\n' '|')"
    exit 1
fi

# Step 2: media_files.find(...) / media_files?.find(...) patterns.
FIND_HITS=$(grep -rnE "media_files\\?\\.find|\\.media_files\\.find" "$DASH_SRC" || true)
if [ -n "$FIND_HITS" ]; then
    step_fail DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES \
        "client-side media_files.find(...) still present: $(echo "$FIND_HITS" | head -3 | tr '\n' '|')"
    exit 1
fi

# Step 3: playback_url must be read somewhere in dashboard src.
PB_HITS_COUNT=$(grep -rn "playback_url" "$DASH_SRC" | wc -l | tr -d ' ')
if [ "$PB_HITS_COUNT" -lt 1 ]; then
    step_fail DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES \
        "no references to playback_url found under $DASH_SRC — dashboard never reads canonical field"
    exit 1
fi

step_pass DASHBOARD_READS_PLAYBACK_URL_NOT_MEDIA_FILES \
    "no pickMasterMediaFile / no media_files.find / playback_url referenced $PB_HITS_COUNT times"
