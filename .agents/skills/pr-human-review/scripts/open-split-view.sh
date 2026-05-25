#!/usr/bin/env bash
# Open a PR Files diff in split view (whitespace-ignored) and print a
# navigation plan for the human reviewer covering only non-test files.
#
# Usage: open-split-view.sh <pr-number> [--repo owner/repo] [--open]
#   --open   xdg-open / open the PR Files URL in the user's default browser.
#            When omitted, just prints the URL + plan.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIST_SCRIPT="$SKILL_DIR/scripts/list-non-test-files.sh"

PR=""
REPO=""
OPEN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --open) OPEN=1; shift ;;
    -h|--help)
      echo "Usage: $0 <pr-number> [--repo owner/repo] [--open]"
      exit 0
      ;;
    *) PR="$1"; shift ;;
  esac
done

[ -z "$PR" ] && { echo "ERROR: pr number required" >&2; exit 2; }
if [ -z "$REPO" ]; then
  REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
fi
[ -z "$REPO" ] && { echo "ERROR: --repo owner/repo required" >&2; exit 2; }

URL="https://github.com/${REPO}/pull/${PR}/files?diff=split&w=1"

# Collect file list + per-file stat
files="$("$LIST_SCRIPT" "$PR" --repo "$REPO")"
[ -z "$files" ] && { echo "ERROR: no non-test files in PR $PR" >&2; exit 1; }

# Fetch diff stats once and grep per file.
stats_tmp="$(mktemp)"
trap 'rm -f "$stats_tmp"' EXIT
gh api "repos/${REPO}/pulls/${PR}/files?per_page=300" --paginate \
  --jq '.[] | "\(.filename)\t\(.additions)\t\(.deletions)"' > "$stats_tmp" 2>/dev/null || true

echo "# Human code review — PR ${REPO}#${PR}"
echo
echo "Split-view URL (whitespace-ignored):"
echo "  $URL"
echo
echo "## Non-test files to review (in order)"
echo
i=0
while IFS= read -r f; do
  i=$((i+1))
  stat="$(awk -F'\t' -v p="$f" '$1==p { printf "+%s -%s", $2, $3 }' "$stats_tmp")"
  [ -z "$stat" ] && stat="-"
  printf '%2d. %s  (%s)\n' "$i" "$f" "$stat"
done <<< "$files"
echo
echo "## How to read"
echo
echo "  1. Open the URL above (or pass --open). The PR loads in split view."
echo "  2. Press 't' in the PR Files tab to fuzzy-find a file from the list."
echo "  3. Read the diff line by line. Hover any line number to leave an inline"
echo "     comment. Click 'Start a review' on the first comment."
echo "  4. When done, top-right 'Submit review' -> Approve / Comment / Request changes."
echo "  5. After GitHub submission, record your verdict in"
echo "     .agents/packs/<pack-id>/code-review.md (reviewer, timestamp, verdict,"
echo "     blast-radius notes, scope-bounded check). Only the human reviewer"
echo "     writes that file."

# Build the filtered local diff so the reviewer can read in VS Code / less /
# delta without GitHub's 100+-file viewer. The list of non-test files came
# from list-non-test-files.sh above; we now reuse it as a --pathspec list to
# `git diff` against the PR's base ref. Falls back to gracefully on
# repos where the head branch isn't checked out locally.
LOCAL_DIFF="/tmp/pr-${PR}-product.diff"
# Find a local clone for the head branch (gh pr view tells us the head ref)
HEAD_REF="$(gh pr view "$PR" --repo "$REPO" --json headRefName --jq .headRefName 2>/dev/null || true)"
BASE_REF="$(gh pr view "$PR" --repo "$REPO" --json baseRefName --jq .baseRefName 2>/dev/null || true)"
# Use the github-supplied diff for portability; filter to keep only product files.
echo
echo "## Filtered diff for offline read"
echo
if gh pr diff "$PR" --repo "$REPO" --patch > "${LOCAL_DIFF}.full" 2>/dev/null; then
  python3 - "${LOCAL_DIFF}.full" "$LOCAL_DIFF" <<PY 2>/dev/null
import sys, re
KEEP = set('''$(printf '%s\n' "$files" | sed "s/'/'\\''/g")'''.strip().splitlines())
with open(sys.argv[1]) as f:
    full = f.read()
chunks = re.split(r'(?m)^(?=diff --git )', full)
kept = [c for c in chunks if any(f' a/{p} b/{p}' in c.split('\n',1)[0] for p in KEEP)]
with open(sys.argv[2], 'w') as f:
    f.write(''.join(kept))
print(f'wrote {len(kept)} file diffs to {sys.argv[2]}')
PY
  rm -f "${LOCAL_DIFF}.full"
  echo "  Open in your editor:"
  echo "    code $LOCAL_DIFF"
  echo "    delta $LOCAL_DIFF"
  echo "    less -R $LOCAL_DIFF"
else
  echo "  (gh pr diff failed — install gh or check repo auth)"
fi

if [ "$OPEN" = "1" ]; then
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 &
  elif command -v open >/dev/null 2>&1; then open "$URL" >/dev/null 2>&1 &
  fi
fi
