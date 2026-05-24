#!/usr/bin/env bash
# List non-test, non-evidence product files in a PR.
# Usage: list-non-test-files.sh <pr-number> [--repo owner/repo]
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERNS_FILE="$SKILL_DIR/references/test-file-patterns.txt"

PR=""
REPO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 <pr-number> [--repo owner/repo]"
      echo
      echo "Outputs non-test product files in the PR, one per line."
      echo "Reads filter patterns from references/test-file-patterns.txt."
      exit 0
      ;;
    *) PR="$1"; shift ;;
  esac
done

[ -z "$PR" ] && { echo "ERROR: pr number required" >&2; exit 2; }

if [ -z "$REPO" ]; then
  # Try to infer from gh default
  REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
fi
[ -z "$REPO" ] && { echo "ERROR: --repo owner/repo required (or run inside a gh-default repo)" >&2; exit 2; }

# Build a single ERE from the patterns file (skip comments and blank lines).
combined_re="$(grep -vE '^\s*(#|$)' "$PATTERNS_FILE" | paste -sd '|' -)"

# List changed files in the PR via the REST files API (gh pr diff has no
# --name-only on older versions; the API endpoint is stable + paginated).
gh api "repos/${REPO}/pulls/${PR}/files?per_page=300" --paginate \
  --jq '.[].filename' \
  | grep -vE "$combined_re" \
  | sort
