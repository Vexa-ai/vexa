#!/usr/bin/env bash
# v0.10.5 Pack X scenario — Pack J coverage gap regression test.
#
# Reproduces the bug discovered 2026-04-27 by live Zoom validation:
# bot completes via /bots/internal/callback/status_change while in
# STOPPING state — the previous handler bypassed Pack J's classifier
# (`_classify_stopped_exit`) for that path, marking the meeting
# `completed/stopped` despite 0 transcripts. Fix: 734d248.
#
# This scenario is the deterministic regression test (uses rig
# primitives — no boilerplate duplication).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../rig.sh"

echo
echo "=== Scenario: pack-j-status-change-bypass ==="

read -r token meeting_id session_uid native_id <<<"$(rig_setup_meeting pack-j-bypass)"
echo "    meeting_id=$meeting_id session=${session_uid:0:8}..."

# Drive through legal transitions: requested → joining → active
rig_drive_to_active "$session_uid" "$native_id"
echo "    drove to active via legal transitions"

# Cross Pack J's 30-second duration threshold
echo "    sleep 35s (cross Pack J duration threshold)..."
sleep 35

# User-stop: active → stopping
rig_delete_bot "$token" google_meet "$native_id" >/dev/null
sleep 1

# THE GAP TRIGGER: bot self-reports completed via status_change while
# in STOPPING. Pre-734d248: meeting marked completed/stopped (silent).
# Post-fix: failed/stopped_with_no_audio.
rig_callback "$session_uid" status_change \
    status=completed \
    reason=self_initiated_leave \
    completion_reason=stopped >/dev/null
echo "    [callback] status_change=completed (the gap-triggering call)"
sleep 2

echo "    asserting Pack J classification..."
if rig_assert_state "$token" "$meeting_id" \
    status=failed \
    completion_reason=stopped_with_no_audio; then
    echo "    ✅ PACK J VERIFIED — status_change path applies _classify_stopped_exit"
    exit 0
else
    echo "    ❌ PACK J BYPASSED — see callbacks.py:531+ branch" >&2
    exit 1
fi
