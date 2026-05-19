#!/usr/bin/env bash
# v0.10.5 FM-001/FM-002/FM-003 — gmeet end-of-meeting nav classified
# correctly through the central classifier instead of the silent NULL bucket.
#
# Pre-fix shape: bot exits with reason="post_join_setup_error" (the gmeet
# end-of-meeting page-navigation crash signature) hit the else branch at
# callbacks.py:311 — `status=failed, completion_reason=NULL,
# failure_stage=ACTIVE` regardless of whether the meeting reached active or
# delivered transcripts. Prod aggregate 7d: 182 NULL-bucket rows (FM-002),
# 127 mislabeled failure_stage rows (FM-003), and the original 11161 case
# (FM-001) where a 30-min meeting with 197 segments delivered painted as
# FAILED.
#
# Post-fix: the else branch routes through _classify_stopped_exit, which
# returns COMPLETED for a meeting that reached active + duration ≥ 30s +
# segments > 0. failure_stage derives from meeting.status at write time.
#
# Asserts the structural fix: 11161-shape input → COMPLETED, not FAILED.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-fm001-gmeet-nav-classified ==="

read -r token meeting_id session_uid native_id <<<"$(rig_setup_meeting pack-fm001)"
echo "    meeting_id=$meeting_id"

rig_drive_to_active "$session_uid" "$native_id"

# Cross duration threshold + seed at least one transcript segment so the
# classifier sees this as a successful meeting (reached_active +
# duration ≥ 30s + segments > 0 → COMPLETED).
sleep 35
rig_seed_transcription "$meeting_id" 1 >/dev/null

# Bot crashes via gmeet end-of-meeting page navigation.
# Maps to meetingFlow.ts:226 — gracefulLeaveFunction(1, "post_join_setup_error").
# Pre-fix: reason was NOT in the allowlist → else branch → FAILED + NULL.
rig_callback "$session_uid" exited \
    exit_code=1 \
    reason=post_join_setup_error >/dev/null
echo "    [callback] exited(reason=post_join_setup_error, exit_code=1)"
sleep 2

# Post-fix expectation: classifier sees reached_active + segments → COMPLETED.
# completion_reason=stopped (default for non-mapped reasons that aren't
# explicit failures).
if rig_assert_state "$token" "$meeting_id" \
    status=completed \
    completion_reason=stopped; then
    echo "    ✅ FM-001/002/003 fix: gmeet end-of-meeting nav classified as COMPLETED"
    exit 0
else
    echo "    ❌ FM-001/002/003 regression: gmeet nav exit not classified through Pack J" >&2
    exit 1
fi
