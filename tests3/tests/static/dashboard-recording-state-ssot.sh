#!/usr/bin/env bash
# dashboard-recording-state-ssot — meeting detail recording state comes from
# canonical meeting.data.recordings, not from transcript availability.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin "dashboard-recording-state-ssot"

store="$ROOT_DIR/services/dashboard/src/stores/meetings-store.ts"
page="$ROOT_DIR/services/dashboard/src/app/meetings/[id]/page.tsx"
viewer="$ROOT_DIR/services/dashboard/src/components/transcript/transcript-viewer.tsx"

if grep -q "function recordingsFromMeeting" "$store" &&
   grep -q "recordings: recordingsFromMeeting(meeting)" "$store" &&
   ! grep -q "existing.find((m) => m.id.toString() === id)" "$store"; then
  step_pass DASHBOARD_DETAIL_FETCHES_CANONICAL_RECORDING_STATE \
    "meeting detail fetch hydrates recordings from meeting.data.recordings instead of list-row state"
else
  step_fail DASHBOARD_DETAIL_FETCHES_CANONICAL_RECORDING_STATE \
    "meeting detail may still trust list rows or fail to hydrate meeting.data.recordings"
fi

if grep -q "effectiveRecordings" "$page" &&
   grep -q "currentMeeting.data?.recordings" "$page" &&
   grep -q "hasActiveRecording" "$page" &&
   grep -q "Recording in progress" "$page"; then
  step_pass DASHBOARD_ACTIVE_RECORDING_UI_USES_MEETING_DATA \
    "meeting page renders active recording state from canonical meeting data before transcript records arrive"
else
  step_fail DASHBOARD_ACTIVE_RECORDING_UI_USES_MEETING_DATA \
    "meeting page does not render active recording state from meeting.data.recordings"
fi

if grep -q "meeting.data?.transcribe_enabled === false" "$viewer" &&
   grep -q "Recording in progress" "$viewer"; then
  step_pass DASHBOARD_DEFERRED_RECORDING_EMPTY_STATE \
    "transcript empty state distinguishes deferred recording from waiting-for-speech"
else
  step_fail DASHBOARD_DEFERRED_RECORDING_EMPTY_STATE \
    "deferred recording can render the generic no-transcript/waiting-for-speech empty state"
fi

test_end
