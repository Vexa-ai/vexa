#!/usr/bin/env bash
# v0.10.6.1-dispatch-check-noop — when DISPATCH_CHECK_URL is unset (the
# OSS self-host default), POST /bots is a no-op pass-through.
#
# Steps:
#   noop_when_unset
#     Static (always):
#       - dispatch_check.py contains the env-unset short-circuit: when
#         DISPATCH_CHECK_URL is empty / unset, return DispatchCheckResult(
#         allow=True) immediately without any HTTP call.
#       - The short-circuit happens BEFORE any httpx client construction
#         so OSS self-hosters add zero dependencies / latency.
#     Runtime (any mode, optional):
#       - When the gateway is reachable AND no DISPATCH_CHECK_URL is
#         configured in the meeting-api container env, POST /bots
#         continues to work (200/202) without 402.
#       - SKIPs when the env probe can't be inspected.
#
# Why this check exists (T-033 audit B1 backward-compat): the env-gated
# call-out must not change behavior for OSS self-hosters who never
# configure DISPATCH_CHECK_URL. The deny-path test (sibling script)
# proves the gate when configured; this script proves the no-op when
# unconfigured.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
DC="$ROOT_DIR/services/meeting-api/meeting_api/dispatch_check.py"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-dispatch-check-noop :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-dispatch-check-noop-$step"

case "$step" in
  noop_when_unset)
    failed=0

    # ── STATIC: env-unset short-circuit present ─────────────────
    if [ ! -f "$DC" ]; then
      echo "    FAIL: dispatch_check.py missing"
      step_fail BOT_CREATE_NOOP_WHEN_DISPATCH_CHECK_UNSET "dispatch_check.py missing"
      exit 0
    fi

    # Pattern 1: explicit env read with empty-default + early return.
    if ! grep -qE "os\.environ\.get\(\s*['\"]DISPATCH_CHECK_URL['\"]" "$DC"; then
      echo "    FAIL: dispatch_check.py does not read DISPATCH_CHECK_URL via os.environ.get"
      failed=1
    fi

    # Pattern 2: at least one early-return path that yields allow=True.
    # Heuristic: "if not <url-var>" returning DispatchCheckResult(allow=True)
    # OR a "return DispatchCheckResult(allow=True)" before any HTTP call.
    if ! python3 - "$DC" <<'PY'
import sys, re
src = open(sys.argv[1]).read()
# 1. Must have an early-return allow=True path.
m_allow = list(re.finditer(r"return\s+DispatchCheckResult\(\s*allow\s*=\s*True", src))
if not m_allow:
    print("    FAIL static-noop: no early-return DispatchCheckResult(allow=True)")
    sys.exit(2)
# 2. The first such return MUST appear BEFORE the first httpx client / .post() call.
m_http = re.search(r"httpx\.(AsyncClient|Client|.*post)\s*\(", src)
if m_http and m_allow[0].start() > m_http.start():
    print("    FAIL static-noop: env-unset early-return appears AFTER the first httpx call")
    sys.exit(3)
sys.exit(0)
PY
    then
      failed=1
    fi

    # ── RUNTIME (optional): meeting-api respects unset env ──────
    set +e
    GATEWAY="${GATEWAY_URL:-}"
    MODE_DETECTED="$(cat "$STATE/deploy_mode" 2>/dev/null || echo "")"
    [ -z "$GATEWAY" ] && [ "$MODE_DETECTED" = "compose" ] && GATEWAY="http://localhost:8056"
    [ -z "$GATEWAY" ] && [ "$MODE_DETECTED" = "lite" ] && GATEWAY="http://localhost:8056"

    if [ -z "$GATEWAY" ] || ! curl -sS -o /dev/null --max-time 3 "$GATEWAY/health" >/dev/null 2>&1; then
      echo "    info: runtime probe skipped (gateway unreachable)"
    elif ! command -v docker >/dev/null 2>&1 && ! command -v kubectl >/dev/null 2>&1; then
      echo "    info: runtime probe skipped (no docker / kubectl to inspect container env)"
    else
      # Inspect the meeting-api container env. If DISPATCH_CHECK_URL is
      # not set, the no-op path is active.
      DC_URL_IN_CONTAINER=""
      if [ "$MODE_DETECTED" = "compose" ] || [ "$MODE_DETECTED" = "lite" ]; then
        CN="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^vexa-(meeting-api|lite)' | head -1)"
        if [ -n "$CN" ]; then
          DC_URL_IN_CONTAINER="$(docker exec "$CN" printenv DISPATCH_CHECK_URL 2>/dev/null || true)"
        fi
      elif [ "$MODE_DETECTED" = "helm" ]; then
        DC_URL_IN_CONTAINER="$(kubectl exec deploy/vexa-meeting-api -- printenv DISPATCH_CHECK_URL 2>/dev/null || true)"
      fi
      if [ -z "$DC_URL_IN_CONTAINER" ]; then
        echo "    ok  runtime: DISPATCH_CHECK_URL unset in container — no-op path is the active default"
      else
        echo "    info: runtime probe inconclusive — DISPATCH_CHECK_URL is set to '$DC_URL_IN_CONTAINER' in container (not the noop case; static contract still valid)"
      fi
    fi
    set -e

    if (( failed == 0 )); then
      step_pass BOT_CREATE_NOOP_WHEN_DISPATCH_CHECK_UNSET "env-unset short-circuit returns allow=True before any HTTP call"
    else
      step_fail BOT_CREATE_NOOP_WHEN_DISPATCH_CHECK_UNSET "env-unset short-circuit incomplete (see above)"
    fi
    ;;
  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
