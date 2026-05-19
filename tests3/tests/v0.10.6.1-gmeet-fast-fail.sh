#!/usr/bin/env bash
# v0.10.6.1-gmeet-fast-fail — host-not-started fast-fail + waiting-room
# eviction detection in the GMeet admission flow.
#
# Steps:
#   rejection_under_30s
#     Static (lite mode):
#       - selectors.ts exports googleHostNotStartedIndicators with at
#         least three "host hasn't started" / "meeting hasn't begun"
#         text patterns.
#       - admission.ts imports it and exposes
#         checkForGoogleHostNotStarted + GMeetAdmissionFailedError.
#       - The waitForGoogleMeetingAdmission flow grants ≤30s grace
#         and throws gmeet_host_not_started if still on the host-not-
#         started page.
#   eviction_retry_or_fail_clean
#     Static (lite mode):
#       - The waiting-room loop tracks everInWaitingRoom and emits
#         gmeet_waiting_room_evicted after a sustained unknown-state
#         window post-eviction.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
SEL="$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/selectors.ts"
ADM="$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/admission.ts"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-gmeet-fast-fail :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-gmeet-fast-fail-$step"

case "$step" in
  rejection_under_30s)
    failed=0
    if ! grep -q "googleHostNotStartedIndicators" "$SEL"; then
      echo "    FAIL: selectors.ts missing googleHostNotStartedIndicators export"
      failed=1
    fi
    # Three text patterns — minimum coverage of the host-not-started copy.
    # File uses TypeScript-escaped apostrophes (hasn\'t), so grep on `hasn`.
    n=$(grep -c "hasn" "$SEL" 2>/dev/null || echo 0)
    if (( n < 3 )); then
      echo "    FAIL: <3 host-not-started text patterns in selectors.ts (found $n)"
      failed=1
    fi
    if ! grep -q "checkForGoogleHostNotStarted" "$ADM"; then
      echo "    FAIL: admission.ts missing checkForGoogleHostNotStarted helper"
      failed=1
    fi
    if ! grep -q "GMeetAdmissionFailedError" "$ADM"; then
      echo "    FAIL: admission.ts missing GMeetAdmissionFailedError sentinel class"
      failed=1
    fi
    if ! grep -q "GMEET_HOST_NOT_STARTED_GRACE_MS = 30_000" "$ADM"; then
      echo "    FAIL: admission.ts grace period not set to 30_000 ms"
      failed=1
    fi
    if ! grep -q '"gmeet_host_not_started"' "$ADM"; then
      echo "    FAIL: admission.ts does not throw gmeet_host_not_started classified reason"
      failed=1
    fi
    if (( failed == 0 )); then
      step_pass GMEET_REJECTION_PAGE_FAST_FAIL_UNDER_30S "host-not-started detection + 30s grace + classified fast-fail wired"
    else
      step_fail GMEET_REJECTION_PAGE_FAST_FAIL_UNDER_30S "one or more checks failed"
    fi
    ;;

  eviction_retry_or_fail_clean)
    failed=0
    if ! grep -q "everInWaitingRoom" "$ADM"; then
      echo "    FAIL: admission.ts does not track everInWaitingRoom"
      failed=1
    fi
    if ! grep -q "GMEET_WAITING_ROOM_EVICTION_GRACE_MS" "$ADM"; then
      echo "    FAIL: admission.ts missing eviction grace period"
      failed=1
    fi
    if ! grep -q '"gmeet_waiting_room_evicted"' "$ADM"; then
      echo "    FAIL: admission.ts does not throw gmeet_waiting_room_evicted classified reason"
      failed=1
    fi
    if (( failed == 0 )); then
      step_pass GMEET_WAITING_ROOM_EVICTION_RETRY_OR_FAIL_CLEAN "eviction detection + classified failure wired"
    else
      step_fail GMEET_WAITING_ROOM_EVICTION_RETRY_OR_FAIL_CLEAN "one or more checks failed"
    fi
    ;;

  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
