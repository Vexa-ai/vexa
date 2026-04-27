#!/usr/bin/env bash
# v0.10.5 Pack X scenario — Pack T terminal-status idempotent re-fire.
#
# Pack T (releases/260427 scope.yaml issue): re-firing the same
# terminal status (completed/failed) on a meeting already in that
# status is idempotent — returns success, doesn't raise, doesn't
# create duplicate transitions. Pre-Pack-T this raised "Invalid
# transition" errors that surfaced as bot crash loops in production.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

native_id="pack-t-idempotent-$(date +%s)-$$"
echo
echo "=== Scenario: pack-t-terminal-idempotency ==="

token=$(rig_get_user_token)
meeting_id=$(rig_spawn_dryrun "$token" "$native_id" google_meet)
session_uid=$(rig_session_bootstrap "$meeting_id")
echo "    meeting_id=$meeting_id"

# Drive to terminal: requested → joining → active → completed
rig_callback "$session_uid" started >/dev/null
sleep 1
rig_callback "$session_uid" exited \
    exit_code=0 \
    reason=self_initiated_leave \
    completion_reason=stopped >/dev/null
sleep 1

# Verify terminal
status=$(rig_get_state "$token" "$meeting_id" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
if [ "$status" != "completed" ] && [ "$status" != "failed" ]; then
    echo "FAIL: meeting not terminal after exit; got '$status'" >&2
    exit 1
fi
echo "    meeting reached terminal status=$status"

# Re-fire the same status — should be idempotent.
result=$(rig_callback "$session_uid" exited \
    exit_code=0 \
    reason=self_initiated_leave \
    completion_reason=stopped 2>&1) || true

# The handler should return 200 (or 200-with-status-already-terminal),
# never 500 / never raise. Curl already filters non-2xx; if curl returned
# nothing, that's a sign of 500 or 422.
if [ -z "$result" ]; then
    # Try without -f to capture HTTP code.
    code=$(curl -s -o /dev/null -w '%{http_code}' \
        -X POST "$BASE/bots/internal/callback/exited" \
        -H "Content-Type: application/json" \
        -d "{\"connection_id\":\"$session_uid\",\"exit_code\":0,\"reason\":\"self_initiated_leave\",\"completion_reason\":\"stopped\"}")
    if [ "$code" = "200" ] || [ "$code" = "201" ] || [ "$code" = "202" ]; then
        echo "    ✓ re-fire returned $code (idempotent terminal acceptance)"
    else
        echo "    ✗ re-fire returned $code (expected 2xx; Pack T regression)" >&2
        exit 1
    fi
else
    echo "    ✓ re-fire returned 2xx (idempotent)"
fi

# Verify meeting is still in the same terminal state — no double-transition.
status_after=$(rig_get_state "$token" "$meeting_id" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
if [ "$status_after" = "$status" ]; then
    echo "    ✓ status stable across re-fire ($status_after)"
else
    echo "    ✗ status flipped: $status → $status_after (Pack T regression)" >&2
    exit 1
fi

echo "    ✅ Pack T idempotent terminal re-fire verified"
exit 0
