#!/usr/bin/env bash
# v0.10.5 Pack X scenario — Pack J classification via exit_callback path.
#
# Companion to pack-j-status-change-bypass.sh. Same input shape but
# the bot fires exit_callback instead of status_change. Both paths
# must produce IDENTICAL classification — locks the invariant.
#
# Caught REAL BUG (2026-04-27): completion_reason was NOT persisted
# to data on FAILED transitions. Fix shipped in update_meeting_status.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-j-via-exit-callback ==="

read -r token meeting_id session_uid native_id <<<"$(rig_setup_meeting pack-j-exit)"
echo "    meeting_id=$meeting_id"

rig_drive_to_active "$session_uid" "$native_id"

# Cross duration threshold
sleep 35

rig_delete_bot "$token" google_meet "$native_id" >/dev/null
sleep 1

# Bot exits with completion_reason=stopped (the canonical exit-callback path).
rig_callback "$session_uid" exited \
    exit_code=0 \
    reason=self_initiated_leave \
    completion_reason=stopped >/dev/null
echo "    [callback] exited(reason=stopped) — Pack J STOPPING-branch"
sleep 2

if rig_assert_state "$token" "$meeting_id" \
    status=failed \
    completion_reason=stopped_with_no_audio; then
    echo "    ✅ exit_callback path produces same classification as status_change path"
    exit 0
else
    echo "    ❌ exit_callback classification regressed" >&2
    exit 1
fi
