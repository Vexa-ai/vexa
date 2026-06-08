#!/usr/bin/env bash
# Pack D (#5) — completion_reason canonicalization.
#
# Proves that the three distinct pre-admission outcome classes each produce
# a unique, actionable completion_reason end-to-end:
#   denial    → awaiting_admission_rejected  (host explicitly denied bot)
#   timeout   → awaiting_admission_timeout   (bot timed out in lobby)
#   join-fail → join_failure                 (bot failed to navigate to meeting)
#
# Invariant: none of the three cases collapse into a shared bucket, and
# all three route to status=failed (not completed).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-d-completion-reason-canonical ==="

fail=0

# ── Case 1: denial ───────────────────────────────────────────────────
echo "  [case 1] denial — bot denied by host while awaiting_admission"
read -r token1 mid1 sess1 nid1 <<<"$(rig_setup_meeting pack-d-denial)"
echo "    meeting_id=$mid1"

rig_callback "$sess1" status_change status=joining container_id="$nid1" >/dev/null
sleep 1
rig_callback "$sess1" status_change status=awaiting_admission container_id="$nid1" >/dev/null
sleep 1

# Bot sends: gracefulLeave(0, "admission_rejected_by_admin") →
# status_change completed + completion_reason=awaiting_admission_rejected
rig_callback "$sess1" status_change \
    status=completed \
    completion_reason=awaiting_admission_rejected \
    reason=admission_rejected_by_admin >/dev/null
sleep 2

if rig_assert_state "$token1" "$mid1" \
    status=failed \
    completion_reason=awaiting_admission_rejected; then
    echo "    ✅ denial → failed/awaiting_admission_rejected"
else
    echo "    ❌ denial case regressed" >&2
    fail=1
fi

# ── Case 2: timeout ──────────────────────────────────────────────────
echo "  [case 2] timeout — bot timed out in lobby"
read -r token2 mid2 sess2 nid2 <<<"$(rig_setup_meeting pack-d-timeout)"
echo "    meeting_id=$mid2"

rig_callback "$sess2" status_change status=joining container_id="$nid2" >/dev/null
sleep 1
rig_callback "$sess2" status_change status=awaiting_admission container_id="$nid2" >/dev/null
sleep 1

# Bot sends: gracefulLeave(0, "admission_timeout") →
# status_change completed + completion_reason=awaiting_admission_timeout
rig_callback "$sess2" status_change \
    status=completed \
    completion_reason=awaiting_admission_timeout \
    reason=admission_timeout >/dev/null
sleep 2

if rig_assert_state "$token2" "$mid2" \
    status=failed \
    completion_reason=awaiting_admission_timeout; then
    echo "    ✅ timeout → failed/awaiting_admission_timeout"
else
    echo "    ❌ timeout case regressed" >&2
    fail=1
fi

# ── Case 3: join-failure ─────────────────────────────────────────────
echo "  [case 3] join-failure — bot failed to navigate to meeting"
read -r token3 mid3 sess3 nid3 <<<"$(rig_setup_meeting pack-d-joinfail)"
echo "    meeting_id=$mid3"

rig_callback "$sess3" status_change status=joining container_id="$nid3" >/dev/null
sleep 1

# Bot sends: gracefulLeave(1, "join_meeting_error") →
# status_change failed + completion_reason=join_failure (Pack D)
rig_callback "$sess3" status_change \
    status=failed \
    reason=join_meeting_error \
    completion_reason=join_failure \
    exit_code=1 >/dev/null
sleep 2

if rig_assert_state "$token3" "$mid3" \
    status=failed \
    completion_reason=join_failure; then
    echo "    ✅ join-failure → failed/join_failure"
else
    echo "    ❌ join-failure case regressed" >&2
    fail=1
fi

# ── Verify three distinct values ────────────────────────────────────
echo "  [verify] three outcome classes produce distinct completion_reasons"
cr1=$(rig_get_state "$token1" "$mid1" | python3 -c "import sys,json; d=json.load(sys.stdin).get('data') or {}; print(d.get('completion_reason',''))")
cr2=$(rig_get_state "$token2" "$mid2" | python3 -c "import sys,json; d=json.load(sys.stdin).get('data') or {}; print(d.get('completion_reason',''))")
cr3=$(rig_get_state "$token3" "$mid3" | python3 -c "import sys,json; d=json.load(sys.stdin).get('data') or {}; print(d.get('completion_reason',''))")

if [ "$cr1" != "$cr2" ] && [ "$cr1" != "$cr3" ] && [ "$cr2" != "$cr3" ]; then
    echo "    ✅ three distinct reasons: $cr1 / $cr2 / $cr3"
else
    echo "    ❌ reasons not distinct: $cr1 / $cr2 / $cr3" >&2
    fail=1
fi

[ $fail -eq 0 ] || exit 1
echo "  ✅ Pack D completion_reason canonical — all 3 outcome classes distinct and accurate"
exit 0
