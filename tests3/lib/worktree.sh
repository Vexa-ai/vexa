#!/usr/bin/env bash
# Per-release git worktrees.
#
# The stage state machine (.current-stage, .state/, throwaway infra labels)
# is tied to a single working directory. Running N releases in parallel from
# one clone therefore requires N worktrees — each gets its own stage file,
# its own .state, its own release-aware infra labels (#229).
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

    # Bootstrap the worktree's stage state: fresh worktree has no
    # .current-stage, so seed it at idle with this release_id. From idle,
    # `make release-groom` is the legal next step.
    python3 "$target/tests3/lib/stage.py" enter idle \
        --release "$rel" --actor worktree-create >/dev/null

    pass "worktree ready: $target (stage=idle, release=$rel)"
    info "next: cd $target && make release-groom ID=$rel"
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
