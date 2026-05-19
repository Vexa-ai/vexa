#!/usr/bin/env bash
# v0.10.6.1-dispatch-check-admin-deny — admin-api bot-create path honors
# DISPATCH_CHECK_URL deny verdicts identically to the meeting-api path.
#
# Steps:
#   admin_bots_endpoint_honors_deny
#     Static (always):
#       - admin-api/app/main.py imports dispatch_check (sibling module).
#       - The admin bot-create handler calls await dispatch_check(...)
#         before any runtime-api spawn / Meeting creation.
#       - On allow=False the handler raises HTTP 402 (or returns the
#         dispatch-denied error envelope consistently with the
#         meeting-api side).
#       - No Stripe / billing / quota / amount semantics in admin-api
#         dispatch path either.
#
# Why this check exists (T-033 audit B7): admin-side bot dispatch had
# no authority call-out — operators could create bots via admin endpoints
# while user-side endpoints honored the gate, creating a side-door.
# This script pins parity between admin-api and meeting-api dispatch.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
ADMIN="$ROOT_DIR/services/admin-api/app/main.py"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-dispatch-check-admin-deny :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-dispatch-check-admin-deny-$step"

case "$step" in
  admin_bots_endpoint_honors_deny)
    failed=0

    if [ ! -f "$ADMIN" ]; then
      echo "    FAIL: admin-api app/main.py missing"
      step_fail ADMIN_BOT_CREATE_HONORS_DISPATCH_CHECK_DENY "admin app/main.py missing"
      exit 0
    fi

    # ── STATIC: import + invocation + 402 + ordering ────────────
    if ! grep -qE "from meeting_api\.dispatch_check import dispatch_check|from \.dispatch_check import dispatch_check" "$ADMIN"; then
      echo "    FAIL: admin app/main.py does not import dispatch_check"
      failed=1
    fi

    # The admin bot-create handler must call dispatch_check.
    if ! grep -q "await dispatch_check(" "$ADMIN"; then
      echo "    FAIL: admin app/main.py does not invoke await dispatch_check(...)"
      failed=1
    fi

    # On deny: 402.
    if ! grep -qE "HTTP_402_PAYMENT_REQUIRED|status_code=402" "$ADMIN"; then
      echo "    FAIL: admin app/main.py has no 402 raise (the deny path)"
      failed=1
    fi

    # No billing semantics leak into OSS admin code path.
    if grep -qiE "stripe|balance|quota|subscription|invoice|customer_id" "$ADMIN"; then
      echo "    FAIL: admin app/main.py contains billing-specific terms — OSS must stay generic"
      grep -iE "stripe|balance|quota|subscription|invoice|customer_id" "$ADMIN" | head -3 | sed 's/^/      /'
      failed=1
    fi

    # Ordering: dispatch_check call appears BEFORE any Meeting creation /
    # runtime-api spawn in the same handler. Heuristic: find the first
    # 'def create_meeting' / 'def create_bot' / handler that mentions
    # both `dispatch_check(` and `Meeting(` or `_spawn` — assert order.
    if ! python3 - "$ADMIN" <<'PY'
import sys, re
src = open(sys.argv[1]).read()
# Find admin handler bodies. Bot-create on admin-api is usually wrapped
# in a function that ends with a runtime-api call. Restrict to functions
# that mention dispatch_check.
funcs = re.split(r"\n(?:async )?def\s+\w+\(", src)
ok = True
saw_relevant = False
for body in funcs:
    if "dispatch_check(" not in body:
        continue
    saw_relevant = True
    dc_pos = body.find("await dispatch_check(")
    # Spawn / Meeting candidates.
    cands = [body.find(s) for s in (
        "_spawn_via_runtime_api(", "runtime_client.create", "Meeting(", "db.add(",
        "create_meeting(", "create_bot(")]
    cands = [p for p in cands if p > 0]
    if cands and dc_pos > min(cands):
        print(f"    FAIL static-order: dispatch_check call AFTER spawn/Meeting in a handler")
        ok = False
        break
if not saw_relevant:
    # No handler with dispatch_check found at all — that's already caught
    # by the import + invocation greps above. Don't double-fail.
    sys.exit(0)
sys.exit(0 if ok else 3)
PY
    then
      failed=1
    fi

    if (( failed == 0 )); then
      step_pass ADMIN_BOT_CREATE_HONORS_DISPATCH_CHECK_DENY "admin import + invocation + 402 + no-billing + pre-creation ordering"
    else
      step_fail ADMIN_BOT_CREATE_HONORS_DISPATCH_CHECK_DENY "one or more checks failed (see above)"
    fi
    ;;
  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
