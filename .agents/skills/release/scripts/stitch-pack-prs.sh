#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  stitch-pack-prs.sh --pack-prs <pack-prs.json> --integration-branch <branch> --base-branch <branch-or-tag> --out <plan.json> [--repo-root <repo>] [--apply]

Dry-run by default. With --apply, checks out/creates the integration branch and
merges pack PR checkout branches with --no-ff merge commits.
USAGE
}

PACK_PRS=""
INTEGRATION_BRANCH=""
BASE_BRANCH=""
OUT=""
REPO_ROOT=""
APPLY="0"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pack-prs) PACK_PRS="${2:-}"; shift 2 ;;
    --integration-branch) INTEGRATION_BRANCH="${2:-}"; shift 2 ;;
    --base-branch) BASE_BRANCH="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --repo-root) REPO_ROOT="${2:-}"; shift 2 ;;
    --apply) APPLY="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$PACK_PRS" ] || { echo "--pack-prs is required" >&2; exit 2; }
[ -n "$INTEGRATION_BRANCH" ] || { echo "--integration-branch is required" >&2; exit 2; }
[ -n "$BASE_BRANCH" ] || { echo "--base-branch is required" >&2; exit 2; }
[ -n "$OUT" ] || { echo "--out is required" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 2; }

if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(git rev-parse --show-toplevel)"
fi

errors=()
if git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$INTEGRATION_BRANCH"; then
  merge_base="$(git -C "$REPO_ROOT" merge-base "$BASE_BRANCH" "$INTEGRATION_BRANCH" || true)"
  if [ -n "$merge_base" ]; then
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      parent_count=$(( $(wc -w <<< "$line") - 1 ))
      commit="$(awk '{print $1}' <<< "$line")"
      if [ "$parent_count" -lt 2 ]; then
        errors+=("direct non-merge commit on integration branch: $commit")
      fi
    done < <(git -C "$REPO_ROOT" rev-list --first-parent --parents "$merge_base..$INTEGRATION_BRANCH")
  fi
fi

pr_refs="$(jq -r '.packs[]?.pr_refs[]?' "$PACK_PRS" | sort -u)"
if [ -z "$pr_refs" ]; then
  errors+=("no pack PR refs to stitch")
fi

if [ "${#errors[@]}" -eq 0 ]; then
  errors_json="[]"
else
  errors_json="$(printf '%s\n' "${errors[@]}" | jq -R . | jq -s .)"
fi
plan="$(jq -n \
  --arg action "$([ "$APPLY" = "1" ] && echo apply || echo dry_run)" \
  --arg integration_branch "$INTEGRATION_BRANCH" \
  --arg base_branch "$BASE_BRANCH" \
  --arg repo_root "$REPO_ROOT" \
  --argjson errors "$errors_json" \
  --slurpfile pack_prs "$PACK_PRS" \
  '{action:$action, integration_branch:$integration_branch, base_branch:$base_branch, repo_root:$repo_root, errors:$errors, packs:$pack_prs[0].packs}')"

mkdir -p "$(dirname "$OUT")"
printf '%s\n' "$plan" > "$OUT"
echo "wrote $OUT"

if [ "${#errors[@]}" -gt 0 ]; then
  printf '%s\n' "${errors[@]}" >&2
  exit 1
fi

if [ "$APPLY" != "1" ]; then
  exit 0
fi

if git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$INTEGRATION_BRANCH"; then
  git -C "$REPO_ROOT" checkout "$INTEGRATION_BRANCH"
else
  git -C "$REPO_ROOT" checkout -b "$INTEGRATION_BRANCH" "$BASE_BRANCH"
fi

for ref in $pr_refs; do
  num="${ref#\#}"
  tmp_branch="codex/stitch-pr-$num"
  gh pr checkout "$num" --branch "$tmp_branch"
  git -C "$REPO_ROOT" checkout "$INTEGRATION_BRANCH"
  git -C "$REPO_ROOT" merge --no-ff "$tmp_branch" -m "Merge pack PR #$num into $INTEGRATION_BRANCH"
done
