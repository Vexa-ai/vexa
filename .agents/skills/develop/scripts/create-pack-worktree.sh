#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  create-pack-worktree.sh --pack-json <pack.json> [--repo-root <repo>] [--worktree-root <dir>] [--out <manifest.json>] [--apply]

Dry-run by default. With --apply, creates codex/pack-<pack-id> from the pack
base branch in a sibling isolated worktree.
USAGE
}

PACK_JSON=""
REPO_ROOT=""
WORKTREE_ROOT="/home/dima/dev"
OUT=""
APPLY="0"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pack-json) PACK_JSON="${2:-}"; shift 2 ;;
    --repo-root) REPO_ROOT="${2:-}"; shift 2 ;;
    --worktree-root) WORKTREE_ROOT="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --apply) APPLY="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$PACK_JSON" ] || { echo "--pack-json is required" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 2; }

if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(git rev-parse --show-toplevel)"
fi

pack_id="$(jq -r '.pack_id // .metadata.pack_id // empty' "$PACK_JSON")"
base_branch="$(jq -r '.base_branch // .metadata.base_branch // "main"' "$PACK_JSON")"
integration_branch="$(jq -r '.integration_branch // .metadata.integration_branch // empty' "$PACK_JSON")"
[ -n "$pack_id" ] || { echo "pack_id missing from $PACK_JSON" >&2; exit 2; }

branch="codex/pack-$pack_id"
worktree_path="$WORKTREE_ROOT/vexa-pack-$pack_id"

manifest="$(jq -n \
  --arg action "$([ "$APPLY" = "1" ] && echo apply || echo dry_run)" \
  --arg pack_id "$pack_id" \
  --arg repo_root "$REPO_ROOT" \
  --arg worktree_path "$worktree_path" \
  --arg base_branch "$base_branch" \
  --arg integration_branch "$integration_branch" \
  --arg branch "$branch" \
  '{action:$action, pack_id:$pack_id, repo_root:$repo_root, worktree_path:$worktree_path, base_branch:$base_branch, integration_branch:$integration_branch, branch:$branch}')"

if [ -n "$OUT" ]; then
  mkdir -p "$(dirname "$OUT")"
  printf '%s\n' "$manifest" > "$OUT"
fi

if [ "$APPLY" != "1" ]; then
  printf '%s\n' "$manifest"
  exit 0
fi

if [ -e "$worktree_path" ]; then
  echo "worktree path already exists: $worktree_path" >&2
  git -C "$worktree_path" status --short >/dev/null
  exit 0
fi

if git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$branch"; then
  git -C "$REPO_ROOT" worktree add "$worktree_path" "$branch"
else
  git -C "$REPO_ROOT" worktree add -b "$branch" "$worktree_path" "$base_branch"
fi

echo "created $worktree_path on $branch"
