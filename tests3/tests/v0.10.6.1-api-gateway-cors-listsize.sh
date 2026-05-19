#!/usr/bin/env bash
# v0.10.6.1-api-gateway-cors-listsize — pins the API gateway CORS contract
# and the /meetings list-size cap (the two pieces of the blast-radius row
# "API gateway list-size + CORS").
#
# Steps:
#   cors_wildcard_with_no_creds   Static (services/api-gateway/main.py):
#                                 - CORS_ORIGINS default is "*"
#                                 - CORS_WILDCARD flag derived from raw=="*"
#                                 - allow_credentials = not CORS_WILDCARD
#                                   (correct browser-API behavior — wildcard
#                                    with credentials is rejected by every
#                                    modern browser CORS impl).
#   list_size_capped              Static (services/meeting-api/meeting_api/
#                                 meetings.py): GET /meetings clamps the
#                                 client-supplied `limit` query param to
#                                 max(1, min(limit, 100)) so a malicious
#                                 client can't request a million-row page.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
GATEWAY="$ROOT_DIR/services/api-gateway/main.py"
MEETINGS="$ROOT_DIR/services/meeting-api/meeting_api/meetings.py"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-api-gateway-cors-listsize :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-api-gateway-cors-listsize-$step"

case "$step" in
  cors_wildcard_with_no_creds)
    failed=0

    if [ ! -f "$GATEWAY" ]; then
      echo "    FAIL: api-gateway main.py missing"
      step_fail "GATEWAY_PRESENT" "file not found"
      failed=1
    else
      step_pass "GATEWAY_PRESENT" "api-gateway/main.py present"

      # Default CORS_ORIGINS = "*"
      if ! grep -qE 'os\.getenv\("CORS_ORIGINS",\s*"\*"\)' "$GATEWAY"; then
        echo "    FAIL: CORS_ORIGINS default is not \"*\""
        step_fail "CORS_DEFAULT_WILDCARD" "default not *"
        failed=1
      else
        step_pass "CORS_DEFAULT_WILDCARD" "CORS_ORIGINS defaults to \"*\""
      fi

      # CORS_WILDCARD derived from raw == "*"
      if ! grep -qE 'CORS_WILDCARD\s*=\s*_cors_raw\s*==\s*"\*"' "$GATEWAY"; then
        echo "    FAIL: CORS_WILDCARD flag not derived from raw=='*'"
        step_fail "CORS_WILDCARD_FLAG" "flag derivation missing"
        failed=1
      else
        step_pass "CORS_WILDCARD_FLAG" "CORS_WILDCARD flag derived correctly"
      fi

      # allow_credentials = not CORS_WILDCARD
      if ! grep -qE 'allow_credentials\s*=\s*not CORS_WILDCARD' "$GATEWAY"; then
        echo "    FAIL: allow_credentials not gated on (not CORS_WILDCARD)"
        step_fail "ALLOW_CREDENTIALS_GATED" "credentials not gated"
        failed=1
      else
        step_pass "ALLOW_CREDENTIALS_GATED" "allow_credentials = not CORS_WILDCARD"
      fi
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  list_size_capped)
    failed=0

    if [ ! -f "$MEETINGS" ]; then
      echo "    FAIL: meetings.py missing"
      step_fail "MEETINGS_PRESENT" "file not found"
      failed=1
    else
      step_pass "MEETINGS_PRESENT" "meeting_api/meetings.py present"

      # GET /meetings list endpoint clamps limit to ≤100
      if ! grep -qE 'limit\s*=\s*max\(1,\s*min\(limit,\s*100\)\)' "$MEETINGS"; then
        echo "    FAIL: /meetings does not clamp limit to max(1, min(limit, 100))"
        step_fail "LIMIT_CLAMPED" "limit-clamp expression missing"
        failed=1
      else
        step_pass "LIMIT_CLAMPED" "GET /meetings clamps limit to [1, 100]"
      fi

      # Negative-offset safety
      if ! grep -qE 'offset\s*=\s*max\(0,\s*offset\)' "$MEETINGS"; then
        echo "    FAIL: /meetings does not clamp offset to max(0, offset)"
        step_fail "OFFSET_CLAMPED" "offset-clamp expression missing"
        failed=1
      else
        step_pass "OFFSET_CLAMPED" "GET /meetings clamps offset to ≥0"
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
