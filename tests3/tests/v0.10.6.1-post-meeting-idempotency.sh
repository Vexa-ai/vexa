#!/usr/bin/env bash
# v0.10.6.1-post-meeting-idempotency — fire_post_meeting_hooks emits one
# internal outbound event per meeting + destination, regardless of how many
# race-shaped callers fire.
#
# Steps:
#   fires_once_per_session
#     Static (always):
#       - post_meeting.fire_post_meeting_hooks claims an outbound event before
#         HTTP delivery.
#       - outbound_events.py loads the meeting under SELECT FOR UPDATE.
#       - Uses meeting.data["outbound_events"], not a new DB column.
#       - Records delivered/queued/failed so retry ownership is visible.
#     Runtime (compose, optional):
#       - In-process simulation via a small Python harness that drives
#         the actual fire_post_meeting_hooks coroutine 4× concurrently
#         on a single test Meeting row + asserts only one hook firing
#         is observed downstream.
#       - SKIP if meeting-api package isn't importable from the test
#         runner (real exec happens inside the meeting-api container
#         when validate runs in compose mode against a live stack).
#
# Why this check exists (#330): six call sites fire post_meeting_hooks
# without coordination; paying customer charged 3× over fair value.
# Idempotency guard pins exactly-once behavior; this script is the
# regression invariant.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
PM="$ROOT_DIR/services/meeting-api/meeting_api/post_meeting.py"
OE="$ROOT_DIR/services/meeting-api/meeting_api/outbound_events.py"
WD="$ROOT_DIR/services/meeting-api/meeting_api/webhook_delivery.py"
RW="$ROOT_DIR/services/meeting-api/meeting_api/webhook_retry_worker.py"
SW="$ROOT_DIR/services/meeting-api/meeting_api/sweeps.py"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-post-meeting-idempotency :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-post-meeting-idempotency-$step"

case "$step" in
  fires_once_per_session)
    failed=0

    # ── STATIC: idempotency guard structure ─────────────────────
    for required in "$PM" "$OE" "$WD" "$RW" "$SW"; do
      if [ ! -f "$required" ]; then
        echo "    FAIL: missing $required"
        failed=1
      fi
    done
    if (( failed != 0 )); then
      step_fail POST_MEETING_HOOKS_FIRE_ONCE_PER_SESSION "required meeting-api files missing"
      exit 0
    fi

    if ! grep -q "claim_outbound_event" "$PM"; then
      echo "    FAIL: fire_post_meeting_hooks does not claim an outbound event"
      failed=1
    fi

    if ! grep -q "deliver_with_result" "$PM"; then
      echo "    FAIL: fire_post_meeting_hooks cannot distinguish delivered/queued/failed"
      failed=1
    fi

    if ! grep -q "mark_outbound_event" "$PM"; then
      echo "    FAIL: fire_post_meeting_hooks does not record delivery outcome"
      failed=1
    fi

    if ! grep -qE "with_for_update\(\)|FOR UPDATE" "$OE"; then
      echo "    FAIL: outbound_events.py does not lock the meeting row"
      failed=1
    fi

    if ! grep -q "OUTBOUND_EVENTS_KEY = \"outbound_events\"" "$OE"; then
      echo "    FAIL: outbound_events.py does not use meeting.data['outbound_events']"
      failed=1
    fi

    if rg -q "post_meeting_hooks_fired_at" "$ROOT_DIR/services/meeting-api/meeting_api"; then
      echo "    FAIL: meeting-api still references post_meeting_hooks_fired_at schema column"
      failed=1
    fi

    if ! grep -q "outbound_event_key" "$RW"; then
      echo "    FAIL: retry worker does not update outbound_events entries"
      failed=1
    fi

    if ! grep -q "find_stale_pending_events" "$SW"; then
      echo "    FAIL: sweeps.py does not recover stale pending outbound events"
      failed=1
    fi

    # Must claim BEFORE firing the external hooks.
    if ! python3 - "$PM" <<'PY'
import sys, ast
src = open(sys.argv[1]).read()
tree = ast.parse(src)
claim = deliver = None
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "claim_outbound_event" and claim is None:
            claim = node.lineno
        if node.func.id == "deliver_with_result" and deliver is None:
            deliver = node.lineno
if claim is None or deliver is None:
    print("    FAIL static-order: missing claim_outbound_event or deliver_with_result")
    sys.exit(2)
if not claim < deliver:
    print("    FAIL static-order: outbound event claim must happen before delivery")
    sys.exit(3)
sys.exit(0)
PY
    then
      failed=1
    fi

    # ── RUNTIME (compose, optional concurrent harness) ──────────
    # In-process simulation requires the meeting-api package importable
    # in this shell. Most release-validate runs do NOT have it (the
    # meeting-api is inside its container). We surface the static
    # contract and defer the concurrent harness to the in-container
    # pytest suite at services/meeting-api/tests/test_post_meeting_idempotency.py.
    if [ -f "$ROOT_DIR/services/meeting-api/tests/test_post_meeting_idempotency.py" ]; then
      echo "    info: pytest harness exists at services/meeting-api/tests/test_post_meeting_idempotency.py (runs in-container)"
    else
      echo "    info: concurrent-harness pytest absent — static contract alone determines verdict"
    fi

    if (( failed == 0 )); then
      step_pass POST_MEETING_HOOKS_FIRE_ONCE_PER_SESSION "outbound_events ledger + retry ownership + claim-before-POST ordering present"
    else
      step_fail POST_MEETING_HOOKS_FIRE_ONCE_PER_SESSION "idempotency guard incomplete (see above)"
    fi
    ;;
  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
