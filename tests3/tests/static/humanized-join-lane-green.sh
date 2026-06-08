#!/usr/bin/env bash
# humanized-join-lane-green — Pack 7 structural proofs for humanized join.
# Validates: xdotool/xclip in Dockerfile.lite, humanized module present,
# waitForAnySelector wired, locale-agnostic selectors first.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin "humanized-join-lane-green"

# ── 1. Dockerfile.lite declares xdotool ──────────────────────────
if grep -qE '^\s+xdotool\s*\\?' "$ROOT_DIR/deploy/lite/Dockerfile.lite"; then
  step_pass "HUMANIZED_JOIN_XDOTOOL_IN_DOCKERFILE" "xdotool present in deploy/lite/Dockerfile.lite"
else
  step_fail "HUMANIZED_JOIN_XDOTOOL_IN_DOCKERFILE" "xdotool NOT found in deploy/lite/Dockerfile.lite — humanized join will silently fall back to synthetic input"
fi

# ── 2. Dockerfile.lite declares xclip ────────────────────────────
if grep -qE '^\s+xclip\s*\\?' "$ROOT_DIR/deploy/lite/Dockerfile.lite"; then
  step_pass "HUMANIZED_JOIN_XCLIP_IN_DOCKERFILE" "xclip present in deploy/lite/Dockerfile.lite"
else
  step_fail "HUMANIZED_JOIN_XCLIP_IN_DOCKERFILE" "xclip NOT found in deploy/lite/Dockerfile.lite — humanized fillField will fail"
fi

# ── 3. humanized module index exists ─────────────────────────────
if [ -f "$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/humanized/index.ts" ]; then
  step_pass "HUMANIZED_MODULE_EXISTS" "humanized/index.ts present"
else
  step_fail "HUMANIZED_MODULE_EXISTS" "humanized/index.ts missing — humanized join module not shipped"
fi

# ── 4. HumanizedInteractor exported from index ───────────────────
if grep -q "HumanizedInteractor" "$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/humanized/index.ts"; then
  step_pass "HUMANIZED_INTERACTOR_EXPORTED" "HumanizedInteractor exported from humanized/index.ts"
else
  step_fail "HUMANIZED_INTERACTOR_EXPORTED" "HumanizedInteractor not exported from humanized/index.ts"
fi

# ── 5. join.ts imports HumanizedInteractor ───────────────────────
if grep -q "HumanizedInteractor" "$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/join.ts"; then
  step_pass "HUMANIZED_JOIN_IMPORTS_INTERACTOR" "join.ts imports HumanizedInteractor"
else
  step_fail "HUMANIZED_JOIN_IMPORTS_INTERACTOR" "join.ts does not import HumanizedInteractor"
fi

# ── 6. waitForAnySelector defined and used ───────────────────────
if grep -q "waitForAnySelector" "$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/join.ts"; then
  step_pass "HUMANIZED_JOIN_WAIT_FOR_ANY_SELECTOR" "waitForAnySelector present in join.ts"
else
  step_fail "HUMANIZED_JOIN_WAIT_FOR_ANY_SELECTOR" "waitForAnySelector missing from join.ts — locale-agnostic selector fallback not wired"
fi

# ── 7. Locale-agnostic join selector precedes English fallback ───
SELECTORS_FILE="$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/selectors.ts"
if python3 - "$SELECTORS_FILE" <<'PY'
import sys, re
text = open(sys.argv[1]).read()
block_start = text.find("googleJoinButtonSelectors")
block_end = text.find("googleCameraButtonSelectors")
if block_start < 0 or block_end < 0:
    print("selectors block not found"); sys.exit(1)
block = text[block_start:block_end]
jsname_idx = block.find("button[jsname]")
english_idx = block.find('has-text("Ask to join")')
if jsname_idx < 0:
    print("no locale-agnostic jsname selector found"); sys.exit(1)
if english_idx < 0:
    print("English 'Ask to join' fallback not found"); sys.exit(1)
if jsname_idx >= english_idx:
    print(f"locale-agnostic selector ({jsname_idx}) must come before English fallback ({english_idx})"); sys.exit(1)
print(f"locale-agnostic at offset {jsname_idx}, English fallback at {english_idx}")
PY
then
  step_pass "HUMANIZED_JOIN_LOCALE_AGNOSTIC_SELECTOR_FIRST" "locale-agnostic join selector precedes English-text fallback"
else
  step_fail "HUMANIZED_JOIN_LOCALE_AGNOSTIC_SELECTOR_FIRST" "locale-agnostic join selector must precede English-text fallback in googleJoinButtonSelectors"
fi

# ── 8. humanizer.available() gates xdotool with graceful fallback ─
if grep -q "humanizer.available\|available()" "$ROOT_DIR/services/vexa-bot/core/src/platforms/googlemeet/join.ts"; then
  step_pass "HUMANIZED_JOIN_AVAILABILITY_GATE" "humanizer.available() check present in join.ts"
else
  step_fail "HUMANIZED_JOIN_AVAILABILITY_GATE" "humanizer.available() check missing — no graceful fallback when xdotool unavailable"
fi

test_end
