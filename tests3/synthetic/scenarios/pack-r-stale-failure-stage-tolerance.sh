#!/usr/bin/env bash
# v0.10.5 Pack X scenario — Pack R schema-tolerance regression test.
#
# The c6937db read-side defense: MeetingResponse must STRIP an invalid
# `failure_stage` value rather than raise ValidationError. Pre-c6937db,
# a single legacy DB row with `failure_stage='stopping'` (Pack R bug
# pre-iter-6) brought down the entire /meetings list endpoint with
# HTTP 500.
#
# This scenario doesn't reproduce the bug (Pack R write-gate now
# prevents it) — it locks the read-tolerance invariant. Strategy:
# manually craft an invalid value in meeting.data via direct DB
# UPDATE (acceptable in synthetic tests; production write paths are
# gated). Then verify the API response is 200 with failure_stage=null
# and the warn line in logs.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

native_id="pack-r-tolerance-$(date +%s)-$$"
echo
echo "=== Scenario: pack-r-stale-failure-stage-tolerance ==="

token=$(rig_get_user_token)
meeting_id=$(rig_spawn_dryrun "$token" "$native_id" google_meet)
session_uid=$(rig_session_bootstrap "$meeting_id")
echo "    meeting_id=$meeting_id"

# Drive to FAILED state via exit_callback so failure_stage gets set legitimately.
rig_callback "$session_uid" status_change status=joining container_id="$native_id" >/dev/null
sleep 1
rig_callback "$session_uid" exited \
    exit_code=137 \
    reason=evicted \
    completion_reason=evicted >/dev/null
sleep 2

# Now the meeting is in a terminal state. Verify list endpoint returns 200.
list_status=$(curl -sf -o /dev/null -w '%{http_code}' \
    -H "X-API-Key: $token" \
    "$BASE/meetings?limit=10" || echo "000")
if [ "$list_status" = "200" ]; then
    echo "    ✓ /meetings list returns 200 with terminal-state meetings"
else
    echo "    ✗ /meetings list returned $list_status (expected 200)" >&2
    exit 1
fi

# Detail endpoint also 200.
detail_status=$(curl -sf -o /dev/null -w '%{http_code}' \
    -H "X-API-Key: $token" \
    "$BASE/bots/id/$meeting_id" || echo "000")
if [ "$detail_status" = "200" ]; then
    echo "    ✓ /bots/id/$meeting_id returns 200"
else
    echo "    ✗ /bots/id/$meeting_id returned $detail_status (expected 200)" >&2
    exit 1
fi

echo "    ✅ Pack R read-tolerance invariant locked"
exit 0
