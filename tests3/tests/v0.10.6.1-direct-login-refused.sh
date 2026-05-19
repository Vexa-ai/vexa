#!/usr/bin/env bash
# v0.10.6.1-direct-login-refused — D13 prove for D6-B blocker closure.
#
# Pins the contract that dashboard /api/auth/send-magic-link refuses direct
# (no-SMTP) login unless VEXA_ALLOW_DIRECT_LOGIN is explicitly truthy.
#
# Steps:
#   no_hostname_fallback   Static: the send-magic-link route's
#                          isDirectLoginAllowed() does NOT inspect
#                          request.headers.get("host"). The hostname
#                          fallback was the regression class — closed in
#                          commit 714dada.
#   helm_default_false     Static: deploy/helm/charts/vexa/values.yaml
#                          sets dashboard.env.VEXA_ALLOW_DIRECT_LOGIN="false"
#                          so operator helm overrides must be explicit.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
ROUTE="$ROOT_DIR/services/dashboard/src/app/api/auth/send-magic-link/route.ts"
VALUES="$ROOT_DIR/deploy/helm/charts/vexa/values.yaml"
VALUES_TEST="$ROOT_DIR/deploy/helm/charts/vexa/values-test.yaml"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-direct-login-refused :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-direct-login-refused-$step"

case "$step" in
  no_hostname_fallback)
    failed=0

    if [ ! -f "$ROUTE" ]; then
      echo "    FAIL: send-magic-link/route.ts missing at $ROUTE"
      step_fail "ROUTE_FILE_PRESENT" "send-magic-link/route.ts not found"
      failed=1
    else
      # The function must exist and must NOT reference request.headers.get("host")
      # inside isDirectLoginAllowed (the regression-class fallback).
      if ! grep -q 'function isDirectLoginAllowed' "$ROUTE"; then
        echo "    FAIL: isDirectLoginAllowed function missing"
        step_fail "ISDIRECTLOGIN_FN_PRESENT" "function declaration not found"
        failed=1
      else
        # Extract the function body (heuristic: 25 lines after the declaration)
        body="$(grep -A 25 'function isDirectLoginAllowed' "$ROUTE" | head -25)"
        if printf '%s' "$body" | grep -qE 'headers\.get\("host"\)|hostname|127\.0\.0\.1|localhost'; then
          echo "    FAIL: isDirectLoginAllowed contains hostname-fallback class — regression!"
          step_fail "NO_HOSTNAME_FALLBACK" "found host/hostname/localhost in fn body"
          failed=1
        else
          step_pass "NO_HOSTNAME_FALLBACK" "isDirectLoginAllowed has no hostname fallback"
        fi
        # The function must consult VEXA_ALLOW_DIRECT_LOGIN explicitly.
        if ! printf '%s' "$body" | grep -q 'VEXA_ALLOW_DIRECT_LOGIN'; then
          echo "    FAIL: isDirectLoginAllowed does not reference VEXA_ALLOW_DIRECT_LOGIN"
          step_fail "FLAG_GATED" "env-var not referenced"
          failed=1
        else
          step_pass "FLAG_GATED" "isDirectLoginAllowed gates on VEXA_ALLOW_DIRECT_LOGIN"
        fi
      fi

      # Route must short-circuit to 503 on !smtp + !allowDirectLogin
      if ! grep -q 'AUTH_PROVIDER_NOT_CONFIGURED' "$ROUTE"; then
        echo "    FAIL: route missing AUTH_PROVIDER_NOT_CONFIGURED refusal path"
        step_fail "REFUSAL_PATH_PRESENT" "AUTH_PROVIDER_NOT_CONFIGURED 503 missing"
        failed=1
      else
        step_pass "REFUSAL_PATH_PRESENT" "route surfaces explicit 503 refusal"
      fi
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  helm_default_false)
    failed=0

    if [ ! -f "$VALUES" ]; then
      echo "    FAIL: $VALUES missing"
      step_fail "VALUES_PRESENT" "helm values.yaml not found"
      failed=1
    else
      if ! grep -E 'VEXA_ALLOW_DIRECT_LOGIN:\s*"false"' "$VALUES" >/dev/null; then
        echo "    FAIL: default helm values.yaml must set VEXA_ALLOW_DIRECT_LOGIN: \"false\""
        step_fail "DEFAULT_FALSE" "missing or wrong value in values.yaml"
        failed=1
      else
        step_pass "DEFAULT_FALSE" "values.yaml defaults VEXA_ALLOW_DIRECT_LOGIN to \"false\""
      fi
    fi

    # Test profile may opt in — but it must be explicit, not silent.
    if [ -f "$VALUES_TEST" ]; then
      if grep -E 'VEXA_ALLOW_DIRECT_LOGIN:\s*"true"' "$VALUES_TEST" >/dev/null; then
        step_pass "TEST_PROFILE_EXPLICIT" "values-test.yaml explicitly opts in to direct login"
      else
        step_skip "TEST_PROFILE_EXPLICIT" "values-test.yaml does not override (acceptable)"
      fi
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  *)
    echo "    FAIL: unknown step '$step'"
    test_end
    exit 1
    ;;
esac

test_end
echo ""
echo "  PASS"
