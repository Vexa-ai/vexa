#!/usr/bin/env bash
# Per-release git worktrees.
#
# Convention: `../vexa-<release_id>` on branch `release/<release_id>`.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
source "$ROOT/tests3/lib/common.sh"

worktree_create() {
    local rel="${1:?usage: worktree.sh create <release_id> [base_branch]}"
    # Default base is `main` (last-shipped state), NOT `dev`. Release
    # branches must not inherit other in-flight releases' commits, else
    # "parallel releases from one clone" collapses into "N coupled
    # releases that must ship in order" (#229 triage r2).
    local base="${2:-main}"
    local parent="${ROOT%/*}"
    local target="${parent}/vexa-${rel}"
    local branch="release/${rel}"

    if [ -e "$target" ]; then
        fail "path already exists: $target"
        info "reuse it: cd $target"
        return 1
    fi

    if git -C "$ROOT" show-ref --quiet "refs/heads/${branch}"; then
        info "branch $branch exists — checking out into $target"
        git -C "$ROOT" worktree add "$target" "$branch"
    else
        info "new worktree: $target (branch $branch from $base)"
        git -C "$ROOT" worktree add -b "$branch" "$target" "$base"
    fi

    pass "worktree ready: $target (release=$rel)"
}

worktree_list() {
    git -C "$ROOT" worktree list
}

# ─── Direct execution ─────────────────────────────
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-help}" in
        create) shift; worktree_create "$@" ;;
        list)   worktree_list ;;
        *)      echo "usage: worktree.sh {create <release_id> [base_branch] | list}" >&2; exit 1 ;;
    esac
fi
