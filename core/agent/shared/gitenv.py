"""gitenv.py — the scrubbed environment every git subprocess in this package must run with.

Git HOOKS export ``GIT_DIR`` (and sometimes ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE``) into the hook
process. Any descendant git subprocess that inherits those stops discovering its repo from ``cwd``
and operates on the EXPORTED repo instead — with ``GIT_DIR`` set and no work tree given, git treats
the subprocess's cwd as that repo's WORK TREE. That is exactly how the pre-push gate run once
destroyed a branch: ``.githooks/pre-push`` → ``pnpm gates`` → pytest, and every test's
``git add -A && git commit`` in a tmp workspace rewrote the branch being pushed (~180 junk commits
deleting the tree).

The rule this module enforces: a workspace git op must NEVER trust inherited repo-discovery vars —
pass ``env=scrubbed_git_env(...)`` to every ``subprocess`` git invocation. Identity/config
injection (``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` / ``GIT_CONFIG_*``) is deliberately left alone:
callers and tests set those on purpose, and they cannot re-point the repo.

(``llm/ports.py`` keeps a small module-local twin of this scrub — the llm module imports nothing
from product code so it stays liftable, the same stance as its local ``_git``.)
"""
from __future__ import annotations

import os

# Every env var that redirects git's repo/worktree/index/object discovery away from cwd.
GIT_REPO_DISCOVERY_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def scrubbed_git_env(**overrides: str) -> dict[str, str]:
    """The child env for a git subprocess: ``os.environ`` minus every repo-discovery redirect,
    plus ``overrides`` (e.g. ``GIT_ASKPASS="true"``). Guarantees cwd-based repo discovery."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_REPO_DISCOVERY_VARS}
    env.update(overrides)
    return env
