#!/usr/bin/env bash
# v0.10.5 Pack X scenario — DELETE no-container path applies Pack J.
#
# Surfaced by helm meeting 8 (2026-04-27): when DELETE finds no
# container_name (cached bot_container_id empty AND runtime-api lookup
# misses), the handler went directly active→completed with reason=
# STOPPED. Bypassed Pack J. A meeting active 60s+ with transcribe_
# enabled and 0 transcripts got silently classified as completed/
# stopped — the #255 silent class Pack J was meant to eliminate.
#
# Fix: meetings.py stop_bot() routes the no-container branch through
# _classify_stopped_exit. This scenario locks the invariant: same
# input shape (active 30s+, transcribe_enabled, 0 transcripts) MUST
# classify as failed/stopped_with_no_audio regardless of which DELETE
# code path the handler takes.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-j-delete-no-container ==="

# Spawn dry_run meeting; bot_container_id is None → DELETE will hit the
# no-container branch (the bug surface).
read -r token meeting_id session_uid native_id <<<"$(rig_setup_meeting pack-j-delete-noctr)"
echo "    meeting_id=$meeting_id (dry_run; no container)"

rig_drive_to_active "$session_uid" "$native_id"
echo "    drove to active"

# Cross Pack J duration threshold
sleep 35

# DELETE — no container_name, runtime-api lookup misses → no-container branch
rig_delete_bot "$token" google_meet "$native_id" >/dev/null
sleep 3

# Pre-fix: status=completed completion_reason=stopped
# Post-fix: status=failed completion_reason=stopped_with_no_audio
if rig_assert_state "$token" "$meeting_id" \
    status=failed \
    completion_reason=stopped_with_no_audio; then
    echo "    ✅ DELETE no-container path applies Pack J classifier"
    exit 0
else
    echo "    ❌ DELETE no-container path BYPASSES Pack J — silent class returns" >&2
    echo "    See meetings.py stop_bot() no-container branch — must call _classify_stopped_exit" >&2
    exit 1
fi
