#!/usr/bin/env bash
# v0.10.5 Pack X scenario — Pack T idempotent terminal re-fire.
#
# Pack T contract: re-firing the same terminal status (completed/
# failed) on a meeting already in that status is idempotent. Pre-
# Pack T this raised "Invalid transition" errors that surfaced as
# bot crash loops in production.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-t-terminal-idempotency ==="

read -r token meeting_id session_uid native_id <<<"$(rig_setup_meeting pack-t-idem)"
echo "    meeting_id=$meeting_id"

rig_drive_to_active "$session_uid" "$native_id"

rig_callback "$session_uid" exited \
    exit_code=0 \
    reason=self_initiated_leave \
    completion_reason=stopped >/dev/null
sleep 1

status=$(rig_get_state "$token" "$meeting_id" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
[ "$status" = "completed" ] || [ "$status" = "failed" ] || { echo "FAIL: not terminal; got '$status'" >&2; exit 1; }
echo "    meeting reached terminal status=$status"

# Re-fire same terminal — should be idempotent (200, no transition flip)
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -X POST "$BASE/bots/internal/callback/exited" \
    -H "Content-Type: application/json" \
    -d "{\"connection_id\":\"$session_uid\",\"exit_code\":0,\"reason\":\"self_initiated_leave\",\"completion_reason\":\"stopped\"}")
case "$code" in
    200|201|202) echo "    ✓ re-fire returned $code (idempotent terminal acceptance)" ;;
    *) echo "    ✗ re-fire returned $code (Pack T regression)" >&2; exit 1 ;;
esac

# Status must stay stable across re-fire
status_after=$(rig_get_state "$token" "$meeting_id" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
[ "$status_after" = "$status" ] || { echo "    ✗ status flipped: $status → $status_after" >&2; exit 1; }
echo "    ✓ status stable across re-fire ($status_after)"

echo "    ✅ Pack T idempotent terminal re-fire verified"
exit 0
