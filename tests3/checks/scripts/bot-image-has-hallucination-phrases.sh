#!/usr/bin/env bash
# BOT_IMAGE_HAS_HALLUCINATION_PHRASES — verifies the 4-language hallucination
# phrase corpus exists at the source path the bot image build script copies
# into dist/. Closes FM-274 (production bots shipped with empty hallucination
# corpus → long-form whisper hallucinations leaked to transcripts).
#
# v0.10.5 audit-3 (ARCH-2 2026-04-29 ~11:20 UTC) flagged FM-274 as P0; this
# script is the source-side regression stamp. Pack P fix wires hallucinations/
# into the build via `cp -r src/services/hallucinations dist/services/hallucinations`
# in services/vexa-bot/core/package.json's `build` script. If the source dir
# is empty / missing files, build copies an empty / partial corpus.
#
# Exits 0 pass / non-zero fail. Errors include actionable detail.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PHRASES_DIR="${REPO_ROOT}/services/vexa-bot/core/src/services/hallucinations"

if [[ ! -d "${PHRASES_DIR}" ]]; then
    echo "FAIL: hallucinations corpus directory missing: ${PHRASES_DIR}"
    echo "      FM-274 regression — bot build will produce an empty filter corpus."
    exit 1
fi

REQUIRED_LANGS=(en es pt ru)
MISSING=()
EMPTY=()

for lang in "${REQUIRED_LANGS[@]}"; do
    f="${PHRASES_DIR}/${lang}.txt"
    if [[ ! -f "${f}" ]]; then
        MISSING+=("${lang}.txt")
        continue
    fi
    # File must contain at least one phrase line (non-empty, non-comment).
    # Phrase lines deliberately START WITH whitespace because whisper
    # hallucinations include the leading space; the check excludes blank
    # lines (whitespace-only) and comment lines (`#` after optional spaces).
    if ! grep -qE '^[[:space:]]*[^#[:space:]].+' "${f}" || \
       ! grep -vE '^[[:space:]]*(#.*)?$' "${f}" | grep -q '[^[:space:]]'; then
        EMPTY+=("${lang}.txt")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "FAIL: phrase files missing in ${PHRASES_DIR}: ${MISSING[*]}"
    echo "      FM-274 regression — bot image will ship without filter coverage for these languages."
    exit 1
fi

if [[ ${#EMPTY[@]} -gt 0 ]]; then
    echo "FAIL: phrase files empty (no non-comment lines) in ${PHRASES_DIR}: ${EMPTY[*]}"
    echo "      FM-274 regression — silent hallucination-filter disable for these languages."
    exit 1
fi

echo "OK: ${#REQUIRED_LANGS[@]} language phrase files present + non-empty: ${REQUIRED_LANGS[*]}"
exit 0
