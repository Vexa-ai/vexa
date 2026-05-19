#!/usr/bin/env bash
# v0.10.6.1-bot-cpu-budget — pins the CPU envelope for vexa-bot pods that
# v0.10.6.1 settled on after the Zoom WebGL spike investigation: both bot
# runtime profiles cap CPU at 1500m, and the --in-process-gpu launch flag
# is present in the bot's Chromium argument list (which is what makes the
# 1500m budget hold — without it, Chromium's GPU process drives a bot's
# CPU to ~115%, blowing the limit).
#
# Steps:
#   profiles_cap_cpu_1500m   Static (services/runtime-api/profiles.yaml):
#                            both vexa-bot profile entries declare
#                            cpu_limit: "1500m". This was reverted from
#                            the 4000m Zoom WebGL bump that briefly
#                            shipped during v0.10.5.
#   bot_launches_inprocgpu   Static (services/vexa-bot/core/src/
#                            constans.ts): the Chromium argument list
#                            includes "--in-process-gpu". This flag is
#                            load-bearing for the CPU budget above.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
PROFILES="$ROOT_DIR/services/runtime-api/profiles.yaml"
CONSTS="$ROOT_DIR/services/vexa-bot/core/src/constans.ts"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-bot-cpu-budget :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-bot-cpu-budget-$step"

case "$step" in
  profiles_cap_cpu_1500m)
    failed=0

    if [ ! -f "$PROFILES" ]; then
      echo "    FAIL: runtime-api profiles.yaml missing"
      step_fail "PROFILES_PRESENT" "file not found"
      failed=1
    else
      step_pass "PROFILES_PRESENT" "runtime-api/profiles.yaml present"

      # Count cpu_limit: "1500m" occurrences — expect ≥ 2 (one per bot profile).
      count="$(grep -cE 'cpu_limit:\s*"1500m"' "$PROFILES" || true)"
      if [ "$count" -lt 2 ]; then
        echo "    FAIL: expected ≥ 2 cpu_limit: \"1500m\" entries in profiles.yaml, found $count"
        step_fail "BOT_PROFILES_CAPPED" "only $count of 2+ profiles capped"
        failed=1
      else
        step_pass "BOT_PROFILES_CAPPED" "$count vexa-bot profiles declare cpu_limit: \"1500m\""
      fi

      # No 4000m regression. The Zoom WebGL bump (v0.10.5 iter) was
      # reverted; if a 4000m cpu_limit reappears here, surface as fail.
      if grep -qE 'cpu_limit:\s*"4000m"' "$PROFILES"; then
        echo "    FAIL: 4000m CPU limit regression in profiles.yaml (Zoom WebGL bump)"
        step_fail "NO_4000M_REGRESSION" "4000m limit found"
        failed=1
      else
        step_pass "NO_4000M_REGRESSION" "no 4000m CPU limit in bot profiles"
      fi
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  bot_launches_inprocgpu)
    failed=0

    if [ ! -f "$CONSTS" ]; then
      echo "    FAIL: vexa-bot constans.ts missing"
      step_fail "CONSTS_PRESENT" "file not found"
      failed=1
    else
      step_pass "CONSTS_PRESENT" "vexa-bot/core/src/constans.ts present"

      # The flag is in the active argument list (a code line — not just
      # in a comment). Find a line that literally starts with whitespace
      # then "--in-process-gpu" in quotes.
      if ! grep -qE '^\s*"--in-process-gpu"' "$CONSTS"; then
        echo "    FAIL: --in-process-gpu not present as an active arg in constans.ts"
        step_fail "INPROCGPU_FLAG_ACTIVE" "flag is not in the active arg list"
        failed=1
      else
        step_pass "INPROCGPU_FLAG_ACTIVE" "--in-process-gpu is in the active Chromium arg list"
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
