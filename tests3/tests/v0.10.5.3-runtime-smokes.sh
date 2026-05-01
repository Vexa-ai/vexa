#!/usr/bin/env bash
# v0.10.5.3 runtime smoke checks (Pack M, O, T, C).
#
# All steps require a live test cluster + fixture meeting URLs. When
# fixtures aren't provided (e.g. lite mode running locally), each step
# step_skip's cleanly. The actual validation is documented in
# scope.yaml's human_verify[] for compose/helm modes — the operator
# dispatches a real bot at a real meeting and reads telemetry.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.5.3-runtime-smokes :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.5.3-runtime-smokes-$step"

case "$step" in

  gmeet_long_recording)
    if [ -z "${FIXTURE_GMEET_MULTIPARTY_URL:-}" ]; then
      step_skip BOT_GMEET_LONG_RECORDING_NO_LEAK "FIXTURE_GMEET_MULTIPARTY_URL not set — see scope.yaml human_verify"
      exit 0
    fi
    # TODO: dispatch bot, wait 1500s, query meetings.data.bot_resources, assert peak_memory_bytes < threshold
    step_skip BOT_GMEET_LONG_RECORDING_NO_LEAK "runtime fixture stub — see scope.yaml human_verify"
    ;;

  teams_long_recording)
    if [ -z "${FIXTURE_TEAMS_MULTIPARTY_URL:-}" ]; then
      step_skip BOT_TEAMS_LONG_RECORDING_NO_LEAK "FIXTURE_TEAMS_MULTIPARTY_URL not set — see scope.yaml human_verify"
      exit 0
    fi
    step_skip BOT_TEAMS_LONG_RECORDING_NO_LEAK "runtime fixture stub — see scope.yaml human_verify"
    ;;

  zoom_long_recording)
    if [ -z "${FIXTURE_ZOOM_URL:-}" ]; then
      step_skip BOT_ZOOM_LONG_RECORDING_NO_LEAK "FIXTURE_ZOOM_URL not set — see scope.yaml human_verify"
      exit 0
    fi
    step_skip BOT_ZOOM_LONG_RECORDING_NO_LEAK "runtime fixture stub — see scope.yaml human_verify"
    ;;

  meeting_bot_logs_present)
    if [ -z "${DB_HOST:-}" ]; then
      step_skip MEETING_BOT_LOGS_FIELD_PRESENT_ON_FAILED "DB env not set — see scope.yaml human_verify (compose mode)"
      exit 0
    fi
    # TODO: query SELECT data->>'bot_logs' FROM meetings WHERE status='failed' ORDER BY id DESC LIMIT 1
    # Assert non-null + length > 0
    step_skip MEETING_BOT_LOGS_FIELD_PRESENT_ON_FAILED "DB query stub — see scope.yaml human_verify"
    ;;

  meeting_bot_resources_present)
    if [ -z "${DB_HOST:-}" ]; then
      step_skip MEETING_BOT_RESOURCES_FIELD_PRESENT "DB env not set — see scope.yaml human_verify (compose mode)"
      exit 0
    fi
    # TODO: query SELECT data->>'bot_resources' FROM meetings WHERE status='completed' ORDER BY id DESC LIMIT 1
    step_skip MEETING_BOT_RESOURCES_FIELD_PRESENT "DB query stub — see scope.yaml human_verify"
    ;;

  user_stop_completed)
    if [ -z "${GATEWAY_URL:-}" ] || [ -z "${ADMIN_TOKEN:-}" ]; then
      step_skip MEETING_API_USER_STOP_IS_COMPLETED "GATEWAY_URL + ADMIN_TOKEN required — see scope.yaml human_verify"
      exit 0
    fi
    # TODO: dispatch bot, wait until status=awaiting_admission, DELETE bot,
    # query SELECT status, data->>'completion_reason' FROM meetings WHERE id=...
    # Assert status=completed, completion_reason=stopped_before_admission
    step_skip MEETING_API_USER_STOP_IS_COMPLETED "fixture stub — see scope.yaml human_verify"
    ;;

  *)
    step_fail "v0.10.5.3-runtime-smokes" "unknown step: $step"
    exit 1
    ;;
esac

echo "  ──────────────────────────────────────────────"
echo ""
