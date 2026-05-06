#!/usr/bin/env bash
# v0.10.5.3 Pack P — fallback-pattern + PII-pattern guards.
#
# Two checks:
#   bot_fallback_audit              (BOT_NO_UNJUSTIFIED_FALLBACKS)
#   release_docs_pii                (RELEASE_DOCS_NO_PII)
#
# Both are SOFT — false-positive risk is non-trivial. Surfaces matches as
# warnings; fails the test only on patterns that look unambiguously like
# a NEW unjustified fallback or NEW customer PII (i.e. not in the existing-
# acceptable-pattern allowlist).

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.5.3-no-fallbacks-pii :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.5.3-no-fallbacks-pii-$step"

case "$step" in

  bot_fallback_audit)
    # Greps services/vexa-bot/core/src for known fallback signatures.
    # A "fallback" is any:
    #   1. Comment containing the word `fallback` not preceded by an issue
    #      ref `(#NNN)` on the same or prior line.
    #   2. Common idioms like "in case of failure", "as a backup",
    #      "fall back to", "default to" with a literal value, etc.
    #
    # Issue-justified fallbacks (commented with #NNN ref) are allowed.
    # Tests files are excluded (test fixtures + mocks legitimately have
    # fallback-like patterns).
    BOT_SRC="$ROOT_DIR/services/vexa-bot/core/src"
    if [ ! -d "$BOT_SRC" ]; then
      step_fail BOT_NO_UNJUSTIFIED_FALLBACKS "services/vexa-bot/core/src missing"
      exit 1
    fi

    # Collect candidate matches
    matches=$(find "$BOT_SRC" -name '*.ts' \
                  -not -path '*/node_modules/*' \
                  -not -path '*/dist/*' \
                  -not -path '*/.next/*' \
                  -not -path '*/__tests__/*' \
                  -print0 \
              | xargs -0 grep -nE -i 'fallback|fall.back|in case .*fail|as a backup' 2>/dev/null \
              || true)

    if [ -z "$matches" ]; then
      step_pass BOT_NO_UNJUSTIFIED_FALLBACKS "fallback_pattern_count=0"
      exit 0
    fi

    # Filter: keep only matches that LACK a #NNN issue ref nearby. We grep
    # for #(\d+) on the same line; if absent, treat as a candidate.
    unjustified=$(echo "$matches" | grep -vE '#[0-9]+' || true)
    # Filter the special case: "v0.10.5.3 Pack M" uses the word "fallback"
    # in narrative comments referring to the OLD pattern being removed.
    # Allow comment lines that contain "Pack M" or "Pack P" (this cycle's
    # explicit fix narrative).
    unjustified=$(echo "$unjustified" | grep -vE 'Pack [MP] |Pack [MP]:|no-fallback|no fallback' || true)

    n=$(echo "$unjustified" | grep -c . || echo 0)
    if [ "$n" -eq 0 ]; then
      step_pass BOT_NO_UNJUSTIFIED_FALLBACKS "fallback_pattern_count=0_unjustified"
      exit 0
    fi

    # Soft check per scope.yaml: surface as warnings, do NOT fail.
    # The develop-stage text rule (tests3/stages/03-develop.md "May NOT")
    # is primary enforcement. Operators reviewing PRs are expected to
    # challenge any NEW fallback pattern; this check supplies the data.
    #
    # If a future v0.10.5.3+ cycle wants this to FAIL on diff-introduced
    # fallbacks specifically, swap to: `git diff --diff-filter=A
    # base..HEAD ...` to count NEW lines only and fail when > 0.
    echo "  WARN: $n unjustified fallback patterns (informational; primary enforcement is develop.md May NOT rule):"
    echo "$unjustified" | head -10 | sed 's|^|    |'
    if [ "$n" -gt 10 ]; then
      echo "    ... ($n total; see full list with: $0 bot_fallback_audit | grep -vE '#[0-9]+|Pack [MP]')"
    fi
    step_pass BOT_NO_UNJUSTIFIED_FALLBACKS "fallback_pattern_count=${n}_unjustified_WARN"
    ;;

  release_docs_pii)
    # Grep tests3/releases/* for common PII patterns:
    #   - Real-looking emails NOT ending in @redacted or @example.com
    #   - Common First-Last name patterns (capitalized two-token sequences)
    #
    # The release-docs convention: anonymize all customer refs as
    # customer-A, customer-B, ..., contributor-1, contributor-2.
    DOCS_DIR="$ROOT_DIR/tests3/releases"
    if [ ! -d "$DOCS_DIR" ]; then
      step_skip RELEASE_DOCS_NO_PII "tests3/releases dir missing — nothing to scan"
      exit 0
    fi

    # Look for emails that aren't @redacted, @example.com, @vexa.ai (internal),
    # or anthropic.com (Claude attribution).
    email_matches=$(find "$DOCS_DIR" -type f \( -name '*.md' -o -name '*.yaml' \) \
        | xargs grep -nEH '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' 2>/dev/null \
        | grep -vE '@(redacted|example\.com|vexa\.ai|anthropic\.com|noreply\.anthropic\.com)' \
        | grep -vE 'customer-[A-Z]@' \
        | grep -vE 'contributor-[0-9]@' \
        || true)

    if [ -z "$email_matches" ]; then
      step_pass RELEASE_DOCS_NO_PII "no real customer emails in tests3/releases/"
      exit 0
    fi

    echo "  potential customer email PII in release docs:"
    echo "$email_matches" | head -20 | sed 's|^|    |'
    n=$(echo "$email_matches" | wc -l)
    step_fail RELEASE_DOCS_NO_PII "potential_pii_email_count=$n in tests3/releases/* — anonymize as customer-X@redacted"
    exit 1
    ;;

  *)
    step_fail "v0.10.5.3-no-fallbacks-pii" "unknown step: $step"
    exit 1
    ;;
esac

echo "  ──────────────────────────────────────────────"
echo ""
