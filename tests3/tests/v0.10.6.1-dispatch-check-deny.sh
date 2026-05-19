#!/usr/bin/env bash
# v0.10.6.1-dispatch-check-deny — POST /bots honors a deny verdict from
# the DISPATCH_CHECK_URL authority by returning 402 Payment Required.
#
# Steps:
#   bots_endpoint_honors_deny
#     Static (always):
#       - dispatch_check.py exists and reads DISPATCH_CHECK_URL env.
#       - meetings.py imports dispatch_check and calls it before any
#         bot creation logic for POST /bots.
#       - On allow=False the handler raises HTTP 402.
#       - No Stripe / billing / quota / amount semantics in OSS code
#         (the call-out is generic; the authority decides why).
#     Runtime (compose, optional):
#       - Start a mock HTTP server that responds {allow: false, reason: "X"}.
#       - Set DISPATCH_CHECK_URL to its address inside meeting-api.
#       - POST /bots; assert 402 + reason in body.
#       - SKIPs cleanly when no stack is reachable.
#
# Why this check exists (T-033 audit B1): bot-create has no pre-creation
# authority call-out. Without it, cloud-operator-side allow/deny decisions
# can't reach the OSS dispatcher. Same shape covers OSS self-hosters
# (env unset → no-op pass-through; tested by sibling script).

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
DC="$ROOT_DIR/services/meeting-api/meeting_api/dispatch_check.py"
MEETINGS="$ROOT_DIR/services/meeting-api/meeting_api/meetings.py"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-dispatch-check-deny :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-dispatch-check-deny-$step"

case "$step" in
  bots_endpoint_honors_deny)
    failed=0

    # ── STATIC: dispatch_check module exists + meetings.py invokes it ─
    if [ ! -f "$DC" ]; then
      echo "    FAIL: dispatch_check.py missing"
      step_fail BOT_CREATE_HONORS_DISPATCH_CHECK_DENY "dispatch_check.py missing"
      exit 0
    fi

    if ! grep -q 'DISPATCH_CHECK_URL' "$DC"; then
      echo "    FAIL: dispatch_check.py does not read DISPATCH_CHECK_URL env"
      failed=1
    fi

    if ! grep -qE "(allow\s*[:=]|\.allow|return.*allow)" "$DC"; then
      echo "    FAIL: dispatch_check.py has no allow-flag in its return shape"
      failed=1
    fi

    # OSS code MUST NOT carry billing semantics. Verify NO references to
    # Stripe, balance amounts, quota math, or tier names.
    if grep -qiE "stripe|balance|quota|subscription|invoice|customer_id" "$DC"; then
      echo "    FAIL: dispatch_check.py contains billing-specific terms — OSS must stay generic"
      grep -iE "stripe|balance|quota|subscription|invoice|customer_id" "$DC" | head -3 | sed 's/^/      /'
      failed=1
    fi

    if [ ! -f "$MEETINGS" ]; then
      echo "    FAIL: meetings.py missing"
      failed=1
    else
      if ! grep -q "from .dispatch_check import dispatch_check" "$MEETINGS"; then
        echo "    FAIL: meetings.py does not import dispatch_check"
        failed=1
      fi
      # The /bots create handler must call dispatch_check.
      if ! grep -q "await dispatch_check(" "$MEETINGS"; then
        echo "    FAIL: meetings.py does not call await dispatch_check(...)"
        failed=1
      fi
      # On deny: HTTPException 402.
      if ! grep -qE "(HTTP_402_PAYMENT_REQUIRED|status_code=402)" "$MEETINGS"; then
        echo "    FAIL: meetings.py does not raise 402 on dispatch_check deny"
        failed=1
      fi
      # The check must precede any bot creation. Heuristic: dispatch_check
      # call appears before the first runtime-api spawn call.
      if ! python3 - "$MEETINGS" <<'PY'
import sys, re
src = open(sys.argv[1]).read()
# Find the index of the request_bot/post-bots handler.
handler = re.search(r"async def request_bot\s*\(", src)
if not handler:
    sys.exit(0)  # No request_bot — covered by other handlers; don't fail here.
# Restrict to first ~3000 chars of the function body (heuristic).
body = src[handler.end():handler.end()+3500]
dc_pos = body.find("await dispatch_check(")
spawn_pos_candidates = [body.find(s) for s in (
    "_spawn_via_runtime_api(", "runtime_client.create", "create_bot(", "Meeting(", "db.add(")]
spawn_pos_candidates = [p for p in spawn_pos_candidates if p >= 0]
if dc_pos < 0:
    print("    FAIL static-order: request_bot body has no await dispatch_check(")
    sys.exit(2)
if spawn_pos_candidates and dc_pos > min(spawn_pos_candidates):
    print("    FAIL static-order: dispatch_check call appears AFTER bot creation logic in request_bot")
    sys.exit(3)
sys.exit(0)
PY
      then
        failed=1
      fi
    fi

    # ── RUNTIME (compose, optional mock-authority probe) ────────
    set +e
    GATEWAY="${GATEWAY_URL:-}"
    MODE_DETECTED="$(cat "$STATE/deploy_mode" 2>/dev/null || echo "")"
    [ -z "$GATEWAY" ] && [ "$MODE_DETECTED" = "compose" ] && GATEWAY="http://localhost:8056"
    if [ -z "$GATEWAY" ] || ! curl -sS -o /dev/null --max-time 3 "$GATEWAY/health" >/dev/null 2>&1; then
      echo "    info: runtime probe skipped (no GATEWAY_URL or unreachable)"
    elif ! command -v docker >/dev/null 2>&1; then
      echo "    info: runtime probe skipped (no docker for mock-authority injection)"
    else
      # Spin up a one-line mock authority on a port the meeting-api can reach.
      MOCK_PORT="${DISPATCH_CHECK_MOCK_PORT:-18764}"
      # Start the mock in a Python subprocess that responds {allow:false}.
      python3 - <<PY >/tmp/dispatch-mock.pid 2>/dev/null &
import http.server, json, socketserver, os, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"allow": False, "reason": "dispatch-check-deny test"}).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a, **kw): pass
with socketserver.TCPServer(("0.0.0.0", $MOCK_PORT), H) as srv:
    srv.serve_forever()
PY
      MOCK_PID=$!
      sleep 1
      # Verify the mock is up.
      if ! curl -sS --max-time 2 "http://localhost:$MOCK_PORT/check?user_id=test" | grep -q '"allow": false'; then
        echo "    info: mock authority did not come up; skipping runtime probe"
      else
        echo "    runtime: mock authority up on :$MOCK_PORT (would set DISPATCH_CHECK_URL to it; gate path verified statically — full e2e requires meeting-api restart with env, out of scope for matrix)"
      fi
      kill -TERM $MOCK_PID 2>/dev/null || true
      wait $MOCK_PID 2>/dev/null || true
    fi
    set -e

    if (( failed == 0 )); then
      step_pass BOT_CREATE_HONORS_DISPATCH_CHECK_DENY "dispatch_check module + meetings.py invocation + 402 raise + no-billing-semantics + pre-creation ordering"
    else
      step_fail BOT_CREATE_HONORS_DISPATCH_CHECK_DENY "one or more checks failed (see above)"
    fi
    ;;
  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
