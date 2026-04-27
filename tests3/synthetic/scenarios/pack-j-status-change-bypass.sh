#!/usr/bin/env bash
# v0.10.5 Pack X scenario — Pack J coverage gap regression test.
#
# Reproduces the bug discovered 2026-04-27 by live Zoom validation:
# bot completes via /bots/internal/callback/status_change while in
# STOPPING state — the previous handler bypassed Pack J's classifier
# (`_classify_stopped_exit`) for that path, marking the meeting
# `completed/stopped` despite 0 transcripts (would have classified as
# `failed/stopped_with_no_audio` per the #255 silent-class rule).
#
# Fix in callbacks.py (`734d248`): both callback paths now route
# STOPPING→COMPLETED through the same classifier.
#
# This scenario is the deterministic regression test — runs in <40 s
# without any external platform dependency.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

native_id="pack-j-bypass-$(date +%s)-$$"
echo
echo "=== Scenario: pack-j-status-change-bypass ==="
echo "    native_id=$native_id"

token=$(rig_get_user_token)
[ -n "$token" ] || { echo "FAIL: no token" >&2; exit 1; }
echo "    token acquired"

meeting_id=$(rig_spawn_dryrun "$token" "$native_id" google_meet)
[ -n "$meeting_id" ] || { echo "FAIL: spawn returned empty meeting_id" >&2; exit 1; }
echo "    spawned meeting_id=$meeting_id"

session_uid=$(rig_session_bootstrap "$meeting_id")
[ -n "$session_uid" ] || { echo "FAIL: session bootstrap failed" >&2; exit 1; }
echo "    session_uid=${session_uid:0:8}..."

# Drive lifecycle: requested → joining → active
rig_callback "$session_uid" started >/dev/null
echo "    [callback] started — meeting reaches active"

# Verify we hit active
sleep 1
status=$(rig_get_state "$token" "$meeting_id" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
if [ "$status" != "active" ]; then
    echo "FAIL: expected active after started, got $status" >&2
    exit 1
fi

# Sleep across Pack J's 30-second duration threshold.
echo "    sleep 35s (cross Pack J duration threshold)..."
sleep 35

# User-stop: active → stopping
rig_delete_bot "$token" google_meet "$native_id" >/dev/null
sleep 1

# THE GAP TRIGGER: bot self-reports completed via status_change while in STOPPING.
# Pre-734d248: this path bypassed _classify_stopped_exit → meeting marked
# completed/stopped. Post-fix: same classifier fires → failed/stopped_with_no_audio.
rig_callback "$session_uid" status_change \
    new_status=completed \
    reason=self_initiated_leave \
    completion_reason=stopped >/dev/null
echo "    [callback] status_change=completed (the gap-triggering call)"
sleep 2

# Assert Pack J classified correctly.
echo "    asserting classification..."
if rig_assert_state "$token" "$meeting_id" \
    status=failed \
    completion_reason=stopped_with_no_audio; then
    echo "    ✅ PACK J VERIFIED — status_change path applies _classify_stopped_exit"
    exit 0
else
    echo "    ❌ PACK J BYPASSED — status_change path does NOT apply classifier"
    echo "    See callbacks.py:531+ branch — _classify_stopped_exit must fire"
    echo "    when meeting.status == STOPPING and new_status == COMPLETED."
    exit 1
fi
