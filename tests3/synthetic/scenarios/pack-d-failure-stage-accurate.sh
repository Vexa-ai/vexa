#!/usr/bin/env bash
# Pack D (#5) — failure_stage accuracy.
#
# Proves that failure_stage reflects the REAL stage the bot was in at exit,
# not a write-time stale value. Three stage classes tested:
#   joining           → failure_stage=joining
#   awaiting_admission → failure_stage=awaiting_admission
#   active            → failure_stage=active
#
# The bug: status_change FAILED passed payload.failure_stage (bot's stale
# tracker) to update_meeting_status, which overwrote Pack R's server-side
# derivation. Fix: derive server-side from meeting.status at write-time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-d-failure-stage-accurate ==="

fail=0

# ── Case 1: fails during joining ─────────────────────────────────────
echo "  [case 1] bot fails while in joining state"
read -r token1 mid1 sess1 nid1 <<<"$(rig_setup_meeting pack-d-fs-joining)"
echo "    meeting_id=$mid1"

rig_callback "$sess1" status_change status=joining container_id="$nid1" >/dev/null
sleep 1

# Bot crashes while in joining — sends status_change failed with stale failureStage=joining
# Server must derive failure_stage=joining from meeting.status (joining)
rig_callback "$sess1" status_change \
    status=failed \
    reason=google_meet_error \
    exit_code=1 >/dev/null
sleep 2

if rig_assert_state "$token1" "$mid1" \
    status=failed \
    failure_stage=joining; then
    echo "    ✅ joining-stage failure → failure_stage=joining"
else
    echo "    ❌ joining-stage failure_stage wrong" >&2
    fail=1
fi

# ── Case 2: fails during awaiting_admission ────────────────────────────
echo "  [case 2] bot fails while in awaiting_admission state"
read -r token2 mid2 sess2 nid2 <<<"$(rig_setup_meeting pack-d-fs-admission)"
echo "    meeting_id=$mid2"

rig_callback "$sess2" status_change status=joining container_id="$nid2" >/dev/null
sleep 1
rig_callback "$sess2" status_change status=awaiting_admission container_id="$nid2" >/dev/null
sleep 1

# Bot crashes while in awaiting_admission — send stale failureStage=joining to prove
# server overrides with awaiting_admission from current meeting state
rig_callback "$sess2" status_change \
    status=failed \
    reason=google_meet_error \
    failure_stage=joining \
    exit_code=1 >/dev/null
sleep 2

if rig_assert_state "$token2" "$mid2" \
    status=failed \
    failure_stage=awaiting_admission; then
    echo "    ✅ awaiting_admission-stage failure → failure_stage=awaiting_admission (overrides stale bot value)"
else
    echo "    ❌ awaiting_admission failure_stage wrong (stale bot value leaked through)" >&2
    fail=1
fi

# ── Case 3: fails during active ────────────────────────────────────────
echo "  [case 3] bot fails while in active state"
read -r token3 mid3 sess3 nid3 <<<"$(rig_setup_meeting pack-d-fs-active)"
echo "    meeting_id=$mid3"

rig_drive_to_active "$sess3" "$nid3"

# Bot crashes during active — send stale failureStage=joining to prove
# server overrides with active from current meeting state
rig_callback "$sess3" status_change \
    status=failed \
    reason=post_join_setup_error \
    failure_stage=joining \
    exit_code=1 >/dev/null
sleep 2

if rig_assert_state "$token3" "$mid3" \
    status=failed \
    failure_stage=active; then
    echo "    ✅ active-stage failure → failure_stage=active (overrides stale bot value)"
else
    echo "    ❌ active-stage failure_stage wrong (stale bot value leaked through)" >&2
    fail=1
fi

[ $fail -eq 0 ] || exit 1
echo "  ✅ Pack D failure_stage accurate — all 3 stage classes correct at write-time"
exit 0
