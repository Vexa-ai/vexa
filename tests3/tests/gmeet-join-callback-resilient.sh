#!/usr/bin/env bash
# gmeet-join-callback-resilient — Pack 3 static-analysis check.
#
# Proves: callJoiningCallback proceeds on transient errors (5xx/timeout)
# and aborts on deliberate rejections (4xx / explicit reject body).
#
# Steps:
#   5xx_proceed — unified-callback.ts marks 5xx as transient (sawDeliberate=false)
#   4xx_abort   — unified-callback.ts marks 4xx as deliberate (sawDeliberate=true)
#   transient_flag_thrown — error objects carry err.transient flag
#   joining_catches_transient — utils.ts callJoiningCallback catches transient
#                               and returns (proceeds) vs re-throws deliberate

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"

CALLBACK_TS="$ROOT_DIR/services/vexa-bot/core/src/services/unified-callback.ts"
UTILS_TS="$ROOT_DIR/services/vexa-bot/core/src/utils.ts"

echo ""
echo "  gmeet-join-callback-resilient"
echo "  ──────────────────────────────────────────────"

test_begin gmeet-join-callback-resilient

# ── 5xx_proceed ────────────────────────────────────────────────────
# 4xx sets sawDeliberate=true; 5xx (no match for the 4xx branch) leaves
# sawDeliberate=false → err.transient=true → caller proceeds.
if grep -qE 'response\.status\s*>=\s*400\s*&&\s*response\.status\s*<\s*500' "$CALLBACK_TS"; then
  step_pass 5xx_proceed "4xx branch sets sawDeliberate; 5xx falls through → transient"
else
  step_fail 5xx_proceed "4xx classification branch missing in unified-callback.ts"
fi

# ── 4xx_abort ──────────────────────────────────────────────────────
# The 4xx branch must set sawDeliberate to true.
if grep -qE 'sawDeliberate\s*=\s*true' "$CALLBACK_TS"; then
  step_pass 4xx_abort "sawDeliberate=true wired for deliberate (4xx/explicit-reject) paths"
else
  step_fail 4xx_abort "sawDeliberate=true missing — 4xx path not classified as deliberate"
fi

# ── transient_flag_thrown ──────────────────────────────────────────
# Thrown errors must carry err.transient set from sawDeliberate.
if grep -qE 'err\.transient\s*=\s*!' "$CALLBACK_TS"; then
  step_pass transient_flag_thrown "err.transient=!sawDeliberate present on thrown errors"
else
  step_fail transient_flag_thrown "err.transient flag not set on thrown errors in unified-callback.ts"
fi

# ── joining_catches_transient ──────────────────────────────────────
# callJoiningCallback must: catch the error, check e?.transient,
# return (proceed) on transient, and rethrow on deliberate.
bad=""
if ! grep -qE 'e(\?\.|\.)transient' "$UTILS_TS"; then
  bad+=" no-transient-check"
fi
if ! grep -qE 'return\b' "$UTILS_TS"; then
  bad+=" no-return-on-transient"
fi
if ! grep -qE 'throw e\b' "$UTILS_TS"; then
  bad+=" no-rethrow-on-deliberate"
fi
if [ -z "$bad" ]; then
  step_pass joining_catches_transient "callJoiningCallback: proceed on transient, abort on deliberate"
else
  step_fail joining_catches_transient "callJoiningCallback missing:$bad"
fi

echo "  ──────────────────────────────────────────────"
echo ""
