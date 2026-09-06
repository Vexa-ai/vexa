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
import re

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


# ── transport pinning ─────────────────────────────────────────────────────────────────────────────
# A git URL names a TRANSPORT, and two of git's transports are not network fetches at all: ``ext::``
# runs a shell command, and ``file://`` (or a bare path) reads this host's disk. Neither is disabled
# by default, so a caller-supplied repository reaches them unless we say otherwise.
#
# ``GIT_ALLOW_PROTOCOL`` is the mechanism, and it is deliberately the ONLY one used here. The
# ``protocol.<name>.allow`` config keys express the same intent, but git ignores them entirely when
# ``GIT_ALLOW_PROTOCOL`` is set (verified: an allow-list of ``https:ssh`` still refuses ``file`` with
# ``protocol.file.allow=always`` injected via ``GIT_CONFIG_*``), so injecting both adds no protection
# and would clobber the ``GIT_CONFIG_*`` slots this module promises to leave to its callers.
#
# The list is derived from the URL's OWN transport rather than fixed, which is what lets the fixtures
# keep cloning from a local path while a remote URL can never silently downgrade to one: an
# ``https://`` clone that redirects to ``file://`` or ``ext::`` is refused by git itself.
_URL_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")
_HELPER_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*::")
_SCP_LIKE = re.compile(r"^[A-Za-z0-9._-]+@(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+):")

#: What a remote fetch may ever use. ``file`` is added only for a reference that is itself a local
#: path; ``ext`` and ``git`` appear in no list at all.
REMOTE_TRANSPORTS = ("https", "ssh")


def git_transports_for(url: str | None) -> tuple[str, ...]:
    """The transports git may use to fetch ``url`` — the narrowest set that still lets it succeed."""
    v = (url or "").strip()
    if not v or _HELPER_SCHEME.match(v):
        return REMOTE_TRANSPORTS            # a helper (``ext::``) is named by nothing we allow
    m = _URL_SCHEME.match(v)
    if m:
        scheme = m.group(1).lower()
        # A transport is carried only when the reference ITSELF named it — never as a downgrade
        # target. So an ``https`` URL that redirects to ``http`` or ``file://`` is refused by git,
        # while a reference that was always ``http://`` or ``file://`` still works.
        #
        # This is deliberately NOT the gate on what a caller may ask for: that is
        # ``control_plane.repo_ref.assert_allowed_scheme``, which refuses ``file://`` and ``ext::``
        # outright at every route. The two do different jobs — this one narrows git to what THIS
        # reference needs, that one decides which references a caller may name at all.
        if scheme in ("http", "file"):
            return (*REMOTE_TRANSPORTS, scheme)
        return REMOTE_TRANSPORTS
    if _SCP_LIKE.match(v):
        return REMOTE_TRANSPORTS            # ``git@host:owner/repo`` → ssh
    return (*REMOTE_TRANSPORTS, "file")     # a scheme-less LOCAL path (an opted-in local repo root)


def pinned_git_env(url: str | None, **overrides: str) -> dict[str, str]:
    """:func:`scrubbed_git_env` plus a transport allow-list pinned to what ``url`` legitimately needs.

    Pass this — not ``scrubbed_git_env`` — to every git subprocess that performs a NETWORK op with a
    URL that came, however indirectly, from a caller."""
    env = scrubbed_git_env(**overrides)
    env.setdefault("GIT_ALLOW_PROTOCOL", ":".join(git_transports_for(url)))
    return env
