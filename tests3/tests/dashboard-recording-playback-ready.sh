#!/usr/bin/env bash
# dashboard-recording-playback-ready — completed recording renders player, not processing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

MODE="$(cat "$STATE/deploy_mode" 2>/dev/null || detect_mode)"
DASHBOARD_URL="${DASHBOARD_URL:-$(cat "$STATE/dashboard_url" 2>/dev/null || true)}"
if [ -z "$DASHBOARD_URL" ]; then
  case "$MODE" in
    lite) DASHBOARD_URL="http://localhost:3100" ;;
    compose) DASHBOARD_URL="http://localhost:3001" ;;
    *) DASHBOARD_URL="http://localhost:3001" ;;
  esac
fi

test_begin "dashboard-recording-playback-ready"

MEETING_PAGE_SRC="$ROOT_DIR/services/dashboard/src/app/meetings/[id]/page.tsx"
if grep -q "shouldPollPostMeetingArtifacts" "$MEETING_PAGE_SRC" \
  && grep -q "refreshPostMeetingArtifacts" "$MEETING_PAGE_SRC" \
  && grep -q "window.setInterval(refreshPostMeetingArtifacts, 2500)" "$MEETING_PAGE_SRC"; then
  step_pass DASHBOARD_POST_MEETING_ARTIFACT_POLL_PRESENT "meeting detail page polls post-meeting artifacts until recording playback can render"
else
  step_fail DASHBOARD_POST_MEETING_ARTIFACT_POLL_PRESENT "meeting detail page is missing post-meeting artifact polling; UI can remain stuck on Recording is processing until reload"
fi

if ! node -e "require('playwright')" >/dev/null 2>&1; then
  step_fail DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY "node cannot require playwright; recording playback browser probe cannot run"
  test_end
  exit 0
fi

CHROMIUM_PATH="${PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH:-}"
if [ -z "$CHROMIUM_PATH" ]; then
  CHROMIUM_PATH="$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)"
fi
if [ -n "$CHROMIUM_PATH" ]; then
  export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="$CHROMIUM_PATH"
fi

OUT_FILE="$(mktemp -t dashboard-recording-playback-ready-XXXXXX.log)"
trap 'rm -f "$OUT_FILE"; _flush_test_report' EXIT INT TERM

if DASHBOARD_URL="$DASHBOARD_URL" node "$SCRIPT_DIR/dashboard-recording-playback-ready.mjs" >"$OUT_FILE" 2>&1; then
  MSG="$(tr '\n' ' ' < "$OUT_FILE" | head -c 500)"
  step_pass DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY "$MSG"
  step_pass DASHBOARD_TRANSCRIPT_RENDERED_VISIBLE "$MSG"
  step_pass LOCAL_HUMAN_BROWSER_HANDOFF_ENDPOINTS_SSOT "$MSG"
else
  MSG="$(tr '\n' ' ' < "$OUT_FILE" | head -c 1000)"
  step_fail DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY "$MSG"
  step_fail DASHBOARD_TRANSCRIPT_RENDERED_VISIBLE "$MSG"
  step_fail LOCAL_HUMAN_BROWSER_HANDOFF_ENDPOINTS_SSOT "$MSG"
fi

test_end
