#!/usr/bin/env bash
# v0.10.5 Pack X scenario — Pack J classification via exit_callback path.
#
# Companion to pack-j-status-change-bypass.sh: same input shape (meeting
# was active 30+s with transcribe_enabled and 0 transcripts, then
# stopped) but the bot fires `/bots/internal/callback/exited` instead of
# status_change. Both paths must produce IDENTICAL classification —
# this scenario locks the invariant.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

native_id="pack-j-exit-$(date +%s)-$$"
echo
echo "=== Scenario: pack-j-via-exit-callback ==="

token=$(rig_get_user_token)
meeting_id=$(rig_spawn_dryrun "$token" "$native_id" google_meet)
session_uid=$(rig_session_bootstrap "$meeting_id")
echo "    meeting_id=$meeting_id session=${session_uid:0:8}..."

# Drive through legal transitions: requested → joining → active.
rig_callback "$session_uid" status_change status=joining container_id="$native_id" >/dev/null
sleep 1
rig_callback "$session_uid" status_change status=active container_id="$native_id" >/dev/null
sleep 1

# Cross duration threshold
sleep 35

rig_delete_bot "$token" google_meet "$native_id" >/dev/null
sleep 1

# Bot exits with completion_reason=stopped (the prior canonical path).
rig_callback "$session_uid" exited \
    exit_code=0 \
    reason=self_initiated_leave \
    completion_reason=stopped >/dev/null
echo "    [callback] exited(reason=stopped) — Pack J STOPPING-branch"
sleep 2

if rig_assert_state "$token" "$meeting_id" \
    status=failed \
    completion_reason=stopped_with_no_audio; then
    echo "    ✅ exit_callback path produces same classification as status_change path"
    exit 0
else
    echo "    ❌ exit_callback path classification regressed"
    exit 1
fi
