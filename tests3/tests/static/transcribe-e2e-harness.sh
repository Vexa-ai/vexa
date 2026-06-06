#!/usr/bin/env bash
# transcribe-e2e-harness — Pack 7 structural proofs for e2e transcription pipeline.
# Validates: bot join → transcription segment path is statically complete.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin "transcribe-e2e-harness"

# ── 1. join.ts has screenshot-on-failure for missing controls ─────
if grep -q "screenshot" "$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/join.ts" && \
   grep -q "throw new Error" "$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/join.ts"; then
  step_pass "TRANSCRIBE_E2E_JOIN_FAILS_LOUD" "join.ts screenshots + throws when selectors not found"
else
  step_fail "TRANSCRIBE_E2E_JOIN_FAILS_LOUD" "join.ts must screenshot + throw on selector failure (no silent skip)"
fi

# ── 2. waitForAnySelector covers both name field and join button ──
JOIN_TS="$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/join.ts"
NAME_OK=$(grep -c "googleNameInputSelectors" "$JOIN_TS" || echo 0)
JOIN_OK=$(grep -c "googleJoinButtonSelectors" "$JOIN_TS" || echo 0)
if [ "$NAME_OK" -ge 1 ] && [ "$JOIN_OK" -ge 1 ]; then
  step_pass "TRANSCRIBE_E2E_SELECTORS_WIRED" "both name-input and join-button selector lists used in join.ts"
else
  step_fail "TRANSCRIBE_E2E_SELECTORS_WIRED" "join.ts must use googleNameInputSelectors and googleJoinButtonSelectors (name=$NAME_OK join=$JOIN_OK)"
fi

# ── 3. humanized fillField uses xclip for text entry ─────────────
if grep -q "xclip\|xdotool type\|fillField" "$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/humanized/humanizedInteraction.ts" 2>/dev/null; then
  step_pass "TRANSCRIBE_E2E_HUMANIZED_TEXT_ENTRY" "humanizedInteraction.ts has humanized text-entry (xclip/xdotool type / fillField)"
else
  step_fail "TRANSCRIBE_E2E_HUMANIZED_TEXT_ENTRY" "humanizedInteraction.ts missing humanized text-entry — name field entry will be synthetic"
fi

# ── 4. x11Input.ts tracks simPointer in dryRun mode ─────────────
if grep -q "simPointer" "$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/humanized/x11Input.ts" 2>/dev/null; then
  step_pass "TRANSCRIBE_E2E_X11_SIM_POINTER" "x11Input.ts tracks simPointer for faithful dry-run tests"
else
  step_fail "TRANSCRIBE_E2E_X11_SIM_POINTER" "x11Input.ts missing simPointer tracking — dryRun pointer verification unreliable"
fi

# ── 5. humanized module ships complete (all 6 files) ─────────────
HUMANIZED_DIR="$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/humanized"
REQUIRED_FILES=(humanizedInteraction.ts x11Input.ts index.ts types.ts mocapEngine.ts mocap-data.ts)
MISSING=()
for f in "${REQUIRED_FILES[@]}"; do
  [ -f "$HUMANIZED_DIR/$f" ] || MISSING+=("$f")
done
if [ ${#MISSING[@]} -eq 0 ]; then
  step_pass "TRANSCRIBE_E2E_HUMANIZED_MODULE_COMPLETE" "all 6 humanized module files present"
else
  step_fail "TRANSCRIBE_E2E_HUMANIZED_MODULE_COMPLETE" "missing humanized files: ${MISSING[*]}"
fi

test_end
