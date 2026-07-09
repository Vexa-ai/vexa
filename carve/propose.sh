#!/usr/bin/env bash
# =============================================================================
# carve/propose.sh — the HUMAN+AI APPROVAL GATE for the carve.
# Computes what would be contributed (vs the published checkpoint, or vs nothing
# for a full proposal) and emits carve/PROPOSAL.md:
#   • CANDIDATES  — files added to the open-core (new since checkpoint)
#   • REMOVED     — paths the manifest EXCLUDE drops, with reasons
#   • FLAGGED     — candidates matching FLAG patterns (internal infra / IPs):
#                   sanitize-and-keep OR approve into CARVE_EXCLUDE — never auto-dropped
#   • CONTRIBUTORS— new authors appearing in this delta
#   • RISK SCAN   — secrets, large blobs, dangling refs to excluded paths
# Nothing publishes until a human + AI review this and run sync.sh --push.
#
# Usage:  carve/propose.sh            # full proposal (entire carve)
#         carve/propose.sh --since-checkpoint   # incremental delta vs vexa-core
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/manifest.sh"
OUT="$HERE/PROPOSAL.md"

is_excluded() { local f="$1"; for e in "${CARVE_EXCLUDE[@]}"; do [[ "$f" == "$e" || "$f" == "$e/"* ]] && return 0; done; return 1; }

# candidate file set = INCLUDE tree of SRC_BRANCH, minus EXCLUDE
mapfile -t ALL < <(git -C "$MONO" ls-tree -r --name-only "$SRC_BRANCH" -- "${CARVE_INCLUDE[@]}" | sort)
CAND=(); REMOVED=()
for f in "${ALL[@]}"; do if is_excluded "$f"; then REMOVED+=("$f"); else CAND+=("$f"); fi; done

# flagged candidates (internal-infra patterns)
FLAGGED=(); for f in "${CAND[@]}"; do
  git -C "$MONO" grep -qIE "$CARVE_FLAG_PATTERNS" "$SRC_BRANCH" -- "$f" 2>/dev/null && FLAGGED+=("$f"); done

{
  echo "# Carve contribution proposal"
  echo
  echo "**Source:** \`$MONO@$SRC_BRANCH\` ($(git -C "$MONO" rev-parse --short "$SRC_BRANCH"))  "
  echo "**Target:** \`$CARVE_REMOTE\`  "
  echo "**Candidates:** ${#CAND[@]} files · **Removed:** ${#REMOVED[@]} · **Flagged for review:** ${#FLAGGED[@]}"
  echo
  echo "> Approve by: sanitizing or sign-off on every FLAGGED file, accepting the REMOVED list,"
  echo "> then running \`carve/sync.sh --push\` (or \`carve/seed.sh --push\` for the initial seed)."
  echo
  echo "## ⚑ Flagged — needs human+AI decision (sanitize-and-keep OR add to EXCLUDE)"
  if [ ${#FLAGGED[@]} -eq 0 ]; then echo "_none_"; else
    for f in "${FLAGGED[@]}"; do
      echo "- \`$f\`"
      git -C "$MONO" grep -nIE "$CARVE_FLAG_PATTERNS" "$SRC_BRANCH" -- "$f" 2>/dev/null \
        | sed "s|$SRC_BRANCH:$f:|    L|" | head -4
    done
  fi
  echo
  echo "## ➖ Removed by manifest EXCLUDE"
  if [ ${#REMOVED[@]} -eq 0 ]; then echo "_none_"; else printf '%s\n' "${REMOVED[@]}" | sed 's/^/- `/; s/$/`/'; fi
  echo
  echo "## 👥 Contributors in the carve"
  git -C "$MONO" -c mailmap.file="$CARVE_MAILMAP" log "$SRC_BRANCH" --use-mailmap \
    --format='%aN <%aE>' -- "${CARVE_INCLUDE[@]}" 2>/dev/null \
    | sort | uniq -c | sort -rn | sed 's/^/- /'
  echo
  echo "## 🔍 Risk scan (candidates only)"
  echo "### Large blobs (>256KB)"
  for f in "${CAND[@]}"; do sz=$(git -C "$MONO" cat-file -s "$SRC_BRANCH:$f" 2>/dev/null || echo 0)
    [ "$sz" -gt 262144 ] && printf -- "- %dKB \`%s\`\n" $((sz/1024)) "$f"; done
  echo "### Secret-shaped literals"
  git -C "$MONO" grep -nIE '(api[_-]?key|secret|password|token)[[:space:]]*[:=][[:space:]]*["'\''][A-Za-z0-9_/+]{16,}' \
    "$SRC_BRANCH" -- "${CARVE_INCLUDE[@]}" 2>/dev/null | grep -viE 'test|spec|example|golden|fixture|getenv|environ|process\.env' | head -10 || echo "_none_"
  echo "### Dangling refs to removed/excluded paths"
  for e in "${CARVE_EXCLUDE[@]}" clients/slim clients/dashboard integrations sdks tools; do
    git -C "$MONO" grep -lI "$e" "$SRC_BRANCH" -- "${CARVE_INCLUDE[@]}" 2>/dev/null \
      | grep -v "^$SRC_BRANCH:$e" | sed "s|^$SRC_BRANCH:|    ref→$e: |"; done | sort -u | head -20
} > "$OUT"

echo "✓ wrote $OUT  (candidates=${#CAND[@]} removed=${#REMOVED[@]} flagged=${#FLAGGED[@]})"
