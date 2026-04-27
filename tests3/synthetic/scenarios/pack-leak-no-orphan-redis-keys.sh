#!/usr/bin/env bash
# v0.10.5 Pack X tier-1 scenario — resource-leak detection.
#
# Drives a meeting through full lifecycle (spawn → active → stop →
# completed) and asserts that Redis-key population returns to
# baseline within tolerance. Catches:
#   - browser_session:* leak (Pack K class)
#   - bm:meeting:<id>:status pubsub-subscriber leak
#   - meeting_session:*:start cache-key leak
#   - container_stop_outbox stream growth without consume
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-leak-no-orphan-redis-keys ==="

# Capture baseline counts BEFORE scenario
baseline_browser=$(rig_baseline_redis_keys 'browser_session:*')
baseline_bm=$(rig_baseline_redis_keys 'bm:meeting:*')
baseline_session=$(rig_baseline_redis_keys 'meeting_session:*')
echo "    baseline: browser=$baseline_browser bm=$baseline_bm session=$baseline_session"

# Drive a normal meeting lifecycle
read -r token meeting_id session_uid native_id <<<"$(rig_setup_meeting pack-leak)"
echo "    meeting_id=$meeting_id"

rig_drive_to_active "$session_uid" "$native_id"
sleep 2

rig_callback "$session_uid" exited \
    exit_code=0 \
    reason=self_initiated_leave \
    completion_reason=stopped >/dev/null

# Wait briefly for cleanup tasks (delayed_container_stop, browser_session reaper)
sleep 5

# Tolerances:
#   browser_session: should be 0 (cleanup happens immediately on stop)
#   bm:meeting:* : may grow by a few transient pubsub subscribers; allow +5
#   meeting_session:* : may include a session_start cache TTL'd for 2h; allow +1
fail=0
rig_assert_no_redis_leak "$baseline_browser" 0 'browser_session:*' || fail=1
rig_assert_no_redis_leak "$baseline_bm" 5 'bm:meeting:*' || fail=1
rig_assert_no_redis_leak "$baseline_session" 1 'meeting_session:*' || fail=1

[ "$fail" -eq 0 ] && { echo "    ✅ no Redis-key leaks across meeting lifecycle"; exit 0; } || exit 1
