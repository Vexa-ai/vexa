#!/usr/bin/env bash
# v0.10.5 Pack X scenario — bot self-reported `failed` propagates completion_reason.
#
# Surfaced by lite meeting 28 (2026-04-27): bot fired
# status_change new_status=failed; the callbacks.py FAILED branch
# called update_meeting_status WITHOUT passing completion_reason
# through. Result: data.completion_reason stays empty even when bot
# supplied one (e.g., evicted, awaiting_admission_timeout).
#
# Same silent-classification class as iter-7's COMPLETED-branch fix
# (commit 20fb68d) but on the FAILED surface. Both paths must
# produce identical persistence behavior.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-j-status-change-failed-with-reason ==="

read -r token meeting_id session_uid native_id <<<"$(rig_setup_meeting pack-j-fail-reason)"
echo "    meeting_id=$meeting_id"

rig_drive_to_active "$session_uid" "$native_id"

# Bot self-reports `failed` with explicit completion_reason
rig_callback "$session_uid" status_change \
    status=failed \
    reason=evicted \
    completion_reason=evicted >/dev/null
sleep 2

# Pre-fix: status=failed but completion_reason=empty (silent)
# Post-fix: status=failed completion_reason=evicted
if rig_assert_state "$token" "$meeting_id" \
    status=failed \
    completion_reason=evicted; then
    echo "    ✅ FAILED branch propagates completion_reason"
    exit 0
else
    echo "    ❌ FAILED branch DROPS completion_reason — silent class on FAILED side" >&2
    echo "    See callbacks.py status_change FAILED branch — must pass payload.completion_reason to update_meeting_status" >&2
    exit 1
fi
