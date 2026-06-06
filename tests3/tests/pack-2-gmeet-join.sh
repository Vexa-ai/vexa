#!/usr/bin/env bash
# Pack 2 — GMeet join & admission correctness unit tests.
#
# Proves:
#   gmeet.humanized_join_click_hits      — humanized endpoint verification throws on off-target
#   gmeet.localized_join_selectors       — locale-agnostic selectors precede English fallbacks
#   gmeet.admission_outcome_classified   — AdmissionError emits distinct denial/lobby_timeout/join_failure
#
# No infra required — all checks are static or tsx unit tests.
# Runs in any deployment mode.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
BOT_CORE="$ROOT_DIR/services/vexa-bot/core"
SELECTORS_TS="$BOT_CORE/src/platforms/googlemeet/selectors.ts"
JOIN_TS="$BOT_CORE/src/platforms/googlemeet/join.ts"
ADMISSION_TS="$BOT_CORE/src/platforms/googlemeet/admission.ts"
HUMANIZED_TEST="$BOT_CORE/src/platforms/googlemeet/humanized/humanized.test.ts"
ADMISSION_TEST="$BOT_CORE/src/platforms/googlemeet/admission.test.ts"

echo ""
echo "  pack-2-gmeet-join"
echo "  ──────────────────────────────────────────────"

test_begin pack-2-gmeet-join

# ── gmeet.localized_join_selectors (static) ───────────────────────────────────

# join button: locale-agnostic structural selector must precede English has-text
join_body=$(cat "$SELECTORS_TS" 2>/dev/null || true)
join_block=$(echo "$join_body" | awk '/googleJoinButtonSelectors/,/googleCameraButtonSelectors/')
jsname_pos=$(echo "$join_block" | grep -n 'button\[jsname\]' | head -1 | cut -d: -f1)
english_pos=$(echo "$join_block" | grep -n 'has-text.*Ask to join' | head -1 | cut -d: -f1)

if [ -n "$jsname_pos" ] && [ -n "$english_pos" ] && [ "$jsname_pos" -lt "$english_pos" ]; then
  step_pass gmeet.localized_join_selectors.join_jsname_before_english \
    "googleJoinButtonSelectors: jsname selector (line $jsname_pos) precedes English has-text (line $english_pos)"
else
  step_fail gmeet.localized_join_selectors.join_jsname_before_english \
    "Expected button[jsname] before has-text(Ask to join) in googleJoinButtonSelectors (jsname_pos=$jsname_pos english_pos=$english_pos)"
fi

# name input: locale-agnostic structural selector must precede English aria-label
name_block=$(echo "$join_body" | awk '/googleNameInputSelectors/,/googleMeetingContainerSelectors/')
name_struct_pos=$(echo "$name_block" | grep -n 'input\[jsname\]' | head -1 | cut -d: -f1)
name_english_pos=$(echo "$name_block" | grep -n 'aria-label.*Your name' | head -1 | cut -d: -f1)

if [ -n "$name_struct_pos" ] && [ -n "$name_english_pos" ] && [ "$name_struct_pos" -lt "$name_english_pos" ]; then
  step_pass gmeet.localized_join_selectors.name_jsname_before_english \
    "googleNameInputSelectors: input[jsname] selector (line $name_struct_pos) precedes English aria-label (line $name_english_pos)"
else
  step_fail gmeet.localized_join_selectors.name_jsname_before_english \
    "Expected input[jsname][type=text] before aria-label=Your name in googleNameInputSelectors (struct_pos=$name_struct_pos english_pos=$name_english_pos)"
fi

# ── gmeet.admission_outcome_classified (static) ───────────────────────────────

if grep -q 'AdmissionOutcome' "$ADMISSION_TS" 2>/dev/null; then
  step_pass gmeet.admission_outcome_classified.type_exported \
    "admission.ts exports AdmissionOutcome type"
else
  step_fail gmeet.admission_outcome_classified.type_exported \
    "admission.ts is missing AdmissionOutcome type export"
fi

for outcome in denial lobby_timeout join_failure; do
  if grep -q "\"$outcome\"" "$ADMISSION_TS" 2>/dev/null; then
    step_pass "gmeet.admission_outcome_classified.$outcome" \
      "admission.ts emits outcome '$outcome'"
  else
    step_fail "gmeet.admission_outcome_classified.$outcome" \
      "admission.ts is missing outcome '$outcome'"
  fi
done

# ── gmeet.humanized_join_click_hits (static + unit) ──────────────────────────

if [ -f "$BOT_CORE/src/platforms/googlemeet/humanized/humanizedInteraction.ts" ]; then
  if grep -q 'pointerHitsTarget' "$BOT_CORE/src/platforms/googlemeet/humanized/humanizedInteraction.ts"; then
    step_pass gmeet.humanized_join_click_hits.endpoint_verify_present \
      "humanizedInteraction.ts has pointerHitsTarget endpoint verification"
  else
    step_fail gmeet.humanized_join_click_hits.endpoint_verify_present \
      "humanizedInteraction.ts is missing pointerHitsTarget"
  fi

  if grep -q 'verification FAILED' "$BOT_CORE/src/platforms/googlemeet/humanized/humanizedInteraction.ts"; then
    step_pass gmeet.humanized_join_click_hits.fail_loud \
      "humanizedInteraction.ts fails loud on miss (verification FAILED message)"
  else
    step_fail gmeet.humanized_join_click_hits.fail_loud \
      "humanizedInteraction.ts missing fail-loud message on endpoint miss"
  fi
else
  step_fail gmeet.humanized_join_click_hits.endpoint_verify_present \
    "humanized/humanizedInteraction.ts does not exist"
  step_fail gmeet.humanized_join_click_hits.fail_loud \
    "humanized/humanizedInteraction.ts does not exist"
fi

# Unit test via tsx (no browser, no X server — pure dryRun)
if command -v tsx >/dev/null 2>&1 || [ -x "$BOT_CORE/node_modules/.bin/tsx" ]; then
  TSX="${BOT_CORE}/node_modules/.bin/tsx"
  [ -x "$TSX" ] || TSX="tsx"
  unit_out=$(cd "$BOT_CORE" && "$TSX" src/platforms/googlemeet/humanized/humanized.test.ts 2>&1)
  unit_rc=$?
  if [ "$unit_rc" -eq 0 ]; then
    passed_count=$(echo "$unit_out" | grep -oE '[0-9]+ passed' | tail -1)
    step_pass gmeet.humanized_join_click_hits.unit_tests \
      "humanized.test.ts passed (${passed_count:-all tests})"
  else
    tail_out=$(echo "$unit_out" | tail -5 | tr '\n' ' ')
    step_fail gmeet.humanized_join_click_hits.unit_tests \
      "humanized.test.ts failed — ${tail_out:0:300}"
  fi

  # Also run existing admission structural tests
  adm_out=$(cd "$BOT_CORE" && "$TSX" src/platforms/googlemeet/admission.test.ts 2>&1)
  adm_rc=$?
  if [ "$adm_rc" -eq 0 ]; then
    step_pass gmeet.admission_outcome_classified.admission_tests \
      "admission.test.ts passed"
  else
    tail_adm=$(echo "$adm_out" | tail -5 | tr '\n' ' ')
    step_fail gmeet.admission_outcome_classified.admission_tests \
      "admission.test.ts failed — ${tail_adm:0:300}"
  fi
else
  step_skip gmeet.humanized_join_click_hits.unit_tests \
    "tsx not available on this harness; CI is authoritative for unit tests"
  step_skip gmeet.admission_outcome_classified.admission_tests \
    "tsx not available on this harness; CI is authoritative for unit tests"
fi

echo "  ──────────────────────────────────────────────"
echo ""

test_end
