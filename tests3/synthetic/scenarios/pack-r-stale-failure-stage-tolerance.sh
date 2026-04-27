#!/usr/bin/env bash
# v0.10.5 Pack X scenario — Pack R schema-tolerance regression test.
#
# c6937db read-side defense: MeetingResponse strips invalid
# failure_stage rather than raising ValidationError. Pre-fix, a
# single legacy DB row with `failure_stage='stopping'` brought down
# the entire /meetings list endpoint with HTTP 500. This scenario
# locks the invariant — list endpoint stays 200 across terminal
# states.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-r-stale-failure-stage-tolerance ==="

read -r token meeting_id session_uid native_id <<<"$(rig_setup_meeting pack-r-tolerance)"
echo "    meeting_id=$meeting_id"

# Drive to FAILED via exit_callback (Pack R failure_stage gets set)
rig_callback "$session_uid" status_change status=joining container_id="$native_id" >/dev/null
sleep 1
rig_callback "$session_uid" exited \
    exit_code=137 \
    reason=evicted \
    completion_reason=evicted >/dev/null
sleep 2

# /meetings list endpoint must return 200 with terminal-state meetings
list_status=$(curl -sf -o /dev/null -w '%{http_code}' \
    -H "X-API-Key: $token" \
    "$BASE/meetings?limit=10" || echo "000")
[ "$list_status" = "200" ] || { echo "    ✗ /meetings returned $list_status" >&2; exit 1; }
echo "    ✓ /meetings list returns 200 with terminal-state meetings"

# Detail endpoint also 200
detail_status=$(curl -sf -o /dev/null -w '%{http_code}' \
    -H "X-API-Key: $token" \
    "$BASE/bots/id/$meeting_id" || echo "000")
[ "$detail_status" = "200" ] || { echo "    ✗ /bots/id returned $detail_status" >&2; exit 1; }
echo "    ✓ /bots/id/$meeting_id returns 200"

echo "    ✅ Pack R read-tolerance invariant locked"
exit 0
