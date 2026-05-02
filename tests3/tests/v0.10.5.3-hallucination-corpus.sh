#!/usr/bin/env bash
# v0.10.5.3 Pack ?: bot hallucination corpus integrity guard.
#
# Closes FM-274 regression class permanently by checking ALL THREE failure
# layers that allowed v0.10.0 → v0.10.5.2 to ship with empty hallucination
# filter:
#
#   Layer 1 (CORPUS): services/vexa-bot/core/src/services/hallucinations/
#                     contains the 4-language phrase corpus, each file
#                     non-empty, with at least one real phrase line.
#
#   Layer 2 (GITIGNORE): .gitignore has the
#                        `!services/vexa-bot/core/src/services/hallucinations/*.txt`
#                        exception. Without it, the global `*.txt` ignore
#                        silently drops the corpus on next git add — exactly
#                        how the original regression slipped past CI.
#
#   Layer 3 (BUILD): services/vexa-bot/core/package.json `build` script
#                    uses `&&` for the cp step, NOT `;` with `2>/dev/null`.
#                    Build must FAIL FAST when corpus is missing, not
#                    silently ship empty filter.
#
# History:
#   - 2025-08-31: corpus added at services/WhisperLive/hallucinations/
#   - 2026-04-05 (e55e878): WhisperLive removed; corpus deleted with it.
#   - 2026-04-29: FM-274 detected by static check (orphan in checks/registry.json),
#                 not bound to any DoD → didn't fail any release gate.
#   - 2026-05-02 (this fix): three-layer restoration + this guard registers
#                            in tests3/registry.yaml + bound to bot-lifecycle
#                            DoD so the gate fails fast on regression.
#
# Symptom if this check fails: bot image rebuilds with empty hallucination
# corpus. Long-form whisper hallucinations like "thanks for watching" /
# "subscribe to my channel" leak into prod transcripts.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
CORPUS_DIR="$ROOT_DIR/services/vexa-bot/core/src/services/hallucinations"
GITIGNORE="$ROOT_DIR/.gitignore"
PKG_JSON="$ROOT_DIR/services/vexa-bot/core/package.json"

echo ""
echo "  v0.10.5.3-hallucination-corpus"
echo "  ──────────────────────────────────────────────"

test_begin v0.10.5.3-hallucination-corpus

# ── Layer 1: CORPUS files exist + non-empty ─────────────────────
REQUIRED_LANGS=(en es pt ru)
MISSING=()
EMPTY=()
for lang in "${REQUIRED_LANGS[@]}"; do
    f="$CORPUS_DIR/$lang.txt"
    if [[ ! -f "$f" ]]; then
        MISSING+=("$lang.txt")
        continue
    fi
    # Count non-blank, non-comment lines (real phrase entries).
    PHRASE_COUNT=$(grep -cvE '^[[:space:]]*(#.*)?$' "$f" 2>/dev/null || echo 0)
    if [[ "$PHRASE_COUNT" -lt 5 ]]; then
        EMPTY+=("$lang.txt:$PHRASE_COUNT-phrases")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    step_fail HALLUCINATION_CORPUS_PRESENT "missing files: ${MISSING[*]} — see services/vexa-bot/core/src/services/hallucinations/"
elif [[ ${#EMPTY[@]} -gt 0 ]]; then
    step_fail HALLUCINATION_CORPUS_PRESENT "files near-empty (<5 phrases): ${EMPTY[*]} — partial corpus = leaked hallucinations"
else
    TOTAL_PHRASES=$(grep -cvE '^[[:space:]]*(#.*)?$' "$CORPUS_DIR"/*.txt | awk -F: '{s+=$NF} END{print s}')
    step_pass HALLUCINATION_CORPUS_PRESENT "4 langs × non-empty corpus = $TOTAL_PHRASES phrases"
fi

# ── Layer 2: GITIGNORE exception present ────────────────────────
# The global `*.txt` rule must have a co-located negation for the
# corpus dir. Without it, the corpus silently drops on next git add.
EXPECTED_EXCEPTION='!services/vexa-bot/core/src/services/hallucinations/\*\.txt'
if grep -qE "$EXPECTED_EXCEPTION" "$GITIGNORE"; then
    step_pass HALLUCINATION_CORPUS_GITIGNORE_EXCEPTION ".gitignore exception protects corpus from silent re-disappearance"
else
    step_fail HALLUCINATION_CORPUS_GITIGNORE_EXCEPTION "missing '!services/vexa-bot/core/src/services/hallucinations/*.txt' in .gitignore — global *.txt rule will silently drop corpus on next git add"
fi

# ── Layer 3: BUILD script uses fail-loud `&&`, not silent `;` ───
# The pattern that masked the original regression was:
#   "tsc && cp -r src/services/hallucinations dist/services/hallucinations 2>/dev/null; node build-browser-utils.js"
# `2>/dev/null;` swallowed cp's missing-source error AND continued the
# chain. Build always succeeded even when corpus didn't make it to dist.
# Required pattern:
#   "tsc && cp -r src/services/hallucinations dist/services/hallucinations && node build-browser-utils.js"
BUILD_SCRIPT=$(python3 -c "import json,sys; print(json.load(open('$PKG_JSON'))['scripts'].get('build',''))")

if echo "$BUILD_SCRIPT" | grep -qE 'cp -r src/services/hallucinations.*2>\s*/dev/null\s*;'; then
    step_fail HALLUCINATION_CORPUS_BUILD_FAIL_LOUD "core/package.json build script uses '2>/dev/null;' — silent failure pattern that masked FM-274 regression"
elif echo "$BUILD_SCRIPT" | grep -qE 'cp -r src/services/hallucinations dist/services/hallucinations\s*&&\s*node build-browser-utils\.js'; then
    step_pass HALLUCINATION_CORPUS_BUILD_FAIL_LOUD "build script uses '&&' chain — cp failure aborts build"
else
    step_fail HALLUCINATION_CORPUS_BUILD_FAIL_LOUD "build script does not match expected fail-loud pattern: $BUILD_SCRIPT"
fi

echo "  ──────────────────────────────────────────────"
echo ""
