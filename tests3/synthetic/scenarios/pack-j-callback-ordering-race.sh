#!/usr/bin/env bash
# v0.10.5 Pack X tier-1 scenario — Pack J callback-ordering race.
#
# Exercises rig_parallel: fires status_change=completed and exited
# CONCURRENTLY for the same session. Pack J classifier must produce
# the SAME final state regardless of arrival order — both paths route
# through _classify_stopped_exit per 734d248. This is the
# deterministic-with-real-concurrency variant of pack-j-status-change-
# bypass.sh.
#
# Catches: ordering bugs that surface only when both callbacks race
# in flight (e.g. would catch if status_change wrote a value that
# exit_callback later overwrote with a different classification).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-j-callback-ordering-race (rig_parallel) ==="

read -r token meeting_id session_uid native_id <<<"$(rig_setup_meeting pack-j-race)"
echo "    meeting_id=$meeting_id"

rig_drive_to_active "$session_uid" "$native_id"

# Cross duration threshold for Pack J's STOPPED_WITH_NO_AUDIO branch
sleep 35

rig_delete_bot "$token" google_meet "$native_id" >/dev/null
sleep 1

# RACE: fire status_change=completed AND exit_callback IN PARALLEL.
# Pack J's classifier must be invariant under arrival order.
echo "    racing status_change vs exit_callback in parallel..."
rig_parallel \
    "rig_callback '$session_uid' status_change status=completed reason=self_initiated_leave completion_reason=stopped >/dev/null" \
    "rig_callback '$session_uid' exited exit_code=0 reason=self_initiated_leave completion_reason=stopped >/dev/null" || true
sleep 3

# Both paths route through Pack J → same classification regardless of order.
if rig_assert_state "$token" "$meeting_id" \
    status=failed \
    completion_reason=stopped_with_no_audio; then
    echo "    ✅ classification invariant under callback-ordering race"
    exit 0
else
    echo "    ❌ ordering race produces inconsistent classification" >&2
    exit 1
fi
