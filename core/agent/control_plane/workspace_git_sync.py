"""workspace_git_sync.py — GitHub push / pull / status for ANY workspace that has a home remote.

The counterpart to :mod:`workspace_publish` (which CREATES a GitHub home for a vexa-born workspace).
Once a workspace has an origin — either an ATTACHED clone (``origin``, kept token-free by
``workspace_attach``) or a vexa-born workspace that was PUBLISHED (the ``vexa-publish`` remote) — this
module keeps it in sync with that home:

- **push**  — fast-forward push of the current branch to the home remote (NEVER forces; a diverged
  remote fails loud). The token rides on the URL for the push's duration ONLY via the shared
  ``push_with_token`` mechanic, then is scrubbed (P15).
- **pull**  — fetch the home branch and FAST-FORWARD only. A divergence (local has commits the remote
  doesn't, or vice-versa with local changes) is reported as a conflict — no auto-merge, no rebase, no
  force — so the user resolves it deliberately, matching the push philosophy. The token is used for the
  fetch only and NEVER persisted (we fetch from the authenticated URL as an argument, so no remote or
  ``.git/config`` credential is ever written).
- **status** — ahead/behind counts against the last-known remote-tracking ref, computed LOCALLY (no
  network, no token) so the panel can render ``↑2 ↓1`` cheaply on every poll.

The "home remote" is resolved as ``origin`` first (attached clones), else ``vexa-publish`` (published
vexa-born workspaces) — so one code path serves both. Every error is token-redacted before it leaves.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.adapters import GitPushError, push_with_token
from shared.gitenv import scrubbed_git_env

from control_plane.workspace_publish import PUBLISH_REMOTE, _URL_CREDENTIAL_RE, _display_url

log = logging.getLogger(__name__)

# A dedicated remote name the sync push points at — distinct from ``origin`` AND ``vexa-publish`` so a
# push never clobbers the persisted URL of either home remote (``push_with_token`` scrubs it token-free).
SYNC_PUSH_REMOTE = "vexa-sync"
# Preference order for the "home" remote a workspace syncs with.
_HOME_REMOTES = ("origin", PUBLISH_REMOTE)


class RemoteSyncError(RuntimeError):
    """A push/pull failed. The message is REDACTED of any access token (P15) so it is safe to surface."""


def _redacted(text: str, token: Optional[str]) -> str:
    return text.replace(token, "***") if token else text


def _git(ws: Path, *args: str, token: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in ``ws`` with a scrubbed env + prompts disabled; failures raise a token-redacted
    ``RemoteSyncError`` (unless ``check=False``, which returns the completed process for the caller to read)."""
    proc = subprocess.run(
        ["git", "-C", str(ws), *args], capture_output=True, text=True,
        env=scrubbed_git_env(GIT_ASKPASS="true", GIT_TERMINAL_PROMPT="0"),
    )
    if check and proc.returncode != 0:
        raise RemoteSyncError(_redacted(f"git {' '.join(args)} failed: {proc.stderr.strip()}", token))
    return proc


def _remote_url(ws: Path, remote: str) -> Optional[str]:
    """The token-free URL of ``remote`` (any embedded credential defensively stripped), or None if absent."""
    proc = _git(ws, "remote", "get-url", remote, check=False)
    url = proc.stdout.strip()
    if proc.returncode != 0 or not url:
        return None
    return _URL_CREDENTIAL_RE.sub(r"\1", url)


def home_remote(ws: str | Path) -> Optional[tuple[str, str]]:
    """The workspace's home ``(remote_name, token_free_url)`` — ``origin`` (attached clones) preferred,
    else ``vexa-publish`` (published vexa-born). None when the workspace has no home yet (never attached,
    never published) — the panel then offers *Publish* instead of *Push/Pull*."""
    wsp = Path(ws)
    if not (wsp / ".git").exists():
        return None
    for name in _HOME_REMOTES:
        url = _remote_url(wsp, name)
        if url:
            return name, url
    return None


def _current_branch(ws: Path) -> Optional[str]:
    proc = _git(ws, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    branch = proc.stdout.strip()
    return branch if branch and branch != "HEAD" else None


def _tracking_ref(ws: Path, remote: str, branch: str) -> Optional[str]:
    """``refs/remotes/<remote>/<branch>`` if it resolves (i.e. we've fetched it), else None."""
    ref = f"refs/remotes/{remote}/{branch}"
    proc = _git(ws, "rev-parse", "--verify", "--quiet", ref, check=False)
    return ref if proc.returncode == 0 else None


def _ahead_behind(ws: Path, tracking_ref: str) -> tuple[int, int]:
    """(ahead, behind) — commits on HEAD not on the remote-tracking ref, and vice-versa. Local + cheap."""
    proc = _git(ws, "rev-list", "--left-right", "--count", f"{tracking_ref}...HEAD", check=False)
    parts = proc.stdout.split()
    if proc.returncode != 0 or len(parts) != 2:
        return 0, 0
    behind, ahead = int(parts[0]), int(parts[1])  # left = remote-only (behind), right = HEAD-only (ahead)
    return ahead, behind


@dataclass(frozen=True)
class RemoteStatus:
    """The GitHub-section state for one workspace."""
    has_home: bool           # the workspace has a home remote (origin / vexa-publish) it can push/pull
    remote: Optional[str]    # which remote is the home ('origin' | 'vexa-publish' | None)
    url: Optional[str]       # the token-free home URL (for the Open link), or None
    branch: Optional[str]    # the current branch, or None on a detached HEAD / bare repo
    tracked: bool            # a remote-tracking ref exists (we've fetched at least once → ahead/behind real)
    ahead: int               # local commits the home doesn't have (→ Push)
    behind: int              # home commits we don't have (→ Pull)


def remote_status(ws: str | Path) -> RemoteStatus:
    """The workspace's sync state — read-only, no network, no token. ahead/behind are against the LAST
    fetched tracking ref (``tracked`` is False until the first pull, matching git's own status semantics)."""
    wsp = Path(ws)
    home = home_remote(wsp)
    if home is None:
        return RemoteStatus(False, None, None, _current_branch(wsp) if (wsp / ".git").exists() else None, False, 0, 0)
    remote, url = home
    branch = _current_branch(wsp)
    if not branch:
        return RemoteStatus(True, remote, _display_url(url), None, False, 0, 0)
    ref = _tracking_ref(wsp, remote, branch)
    ahead, behind = _ahead_behind(wsp, ref) if ref else (0, 0)
    return RemoteStatus(True, remote, _display_url(url), branch, ref is not None, ahead, behind)


@dataclass(frozen=True)
class PushResult:
    remote: str
    url: str        # token-free home URL
    branch: str
    head_sha: str


def push_origin(ws: str | Path, *, token: str) -> PushResult:
    """Push the current branch to the workspace's home remote (origin / vexa-publish), full history,
    fast-forward only. The ``token`` authenticates the push and is NEVER persisted (P15); a diverged
    remote fails loud (no force). Attached clones keep their token-free ``origin`` intact throughout."""
    wsp = Path(ws)
    if not (token or "").strip():
        raise ValueError("a GitHub access token is required")  # bad input (API → 400)
    token = token.strip()
    home = home_remote(wsp)
    if home is None:
        raise RemoteSyncError("this workspace has no GitHub home yet — publish or attach a repo first")
    remote, url = home
    branch = _current_branch(wsp)
    if not branch:
        raise RemoteSyncError("workspace is on a detached HEAD — check out a branch to push")
    _git(wsp, "rev-parse", "--verify", "HEAD", token=token)  # at least one commit, or fail loud
    try:
        head_sha = push_with_token(wsp, url, branch, token, remote=SYNC_PUSH_REMOTE)
    except GitPushError as exc:  # already token-redacted (P15)
        msg = str(exc)
        if "non-fast-forward" in msg or "fetch first" in msg or "rejected" in msg:
            raise RemoteSyncError(
                f"push rejected — the remote has commits this workspace doesn't. Pull first (no force push): {msg}"
            ) from None
        raise RemoteSyncError(f"git push failed: {msg}") from None
    # The ff push succeeded, so the home ref now equals HEAD — advance the tracking ref locally so the
    # panel's ahead/behind reflects the push without a follow-up fetch.
    _git(wsp, "update-ref", f"refs/remotes/{remote}/{branch}", head_sha, check=False)
    log.info("workspace push subject-ws=%s remote=%s ref=%s", wsp.name, remote, branch)  # metadata only (P15)
    return PushResult(remote=remote, url=_display_url(url), branch=branch, head_sha=head_sha)


@dataclass(frozen=True)
class PullResult:
    remote: str
    url: str
    branch: str
    head_sha: str
    updated: bool       # HEAD moved (there were remote commits we fast-forwarded to)
    behind_before: int  # how many commits we were behind before the pull


def pull_origin(ws: str | Path, *, token: Optional[str] = None) -> PullResult:
    """Fetch the home branch and FAST-FORWARD only. No merge commit, no rebase, no force: a divergence
    (local commits the remote lacks) is reported as a conflict for the user to resolve. The ``token``
    (optional — public repos need none) is used for the fetch ONLY and NEVER persisted: we fetch from the
    authenticated URL as an argument, so nothing is written to a remote or ``.git/config`` (P15)."""
    wsp = Path(ws)
    home = home_remote(wsp)
    if home is None:
        raise RemoteSyncError("this workspace has no GitHub home yet — publish or attach a repo first")
    remote, url = home
    branch = _current_branch(wsp)
    if not branch:
        raise RemoteSyncError("workspace is on a detached HEAD — check out a branch to pull")
    token = (token or "").strip() or None
    auth_url = url
    if token and "://" in url:
        proto, rest = url.split("://", 1)
        auth_url = f"{proto}://{token}@{rest}"
    # Fetch from the URL directly (not a persisted remote) so the credential never lands anywhere.
    fetch = _git(wsp, "fetch", "--quiet", auth_url, branch, token=token, check=False)
    if fetch.returncode != 0:
        raise RemoteSyncError(_redacted(f"fetch from {remote} failed: {fetch.stderr.strip()}", token))
    fetched = _git(wsp, "rev-parse", "FETCH_HEAD", token=token).stdout.strip()
    # Record the tracking ref so status stays coherent whether or not the merge fast-forwards.
    _git(wsp, "update-ref", f"refs/remotes/{remote}/{branch}", fetched, check=False)
    before = _git(wsp, "rev-parse", "HEAD", token=token).stdout.strip()
    # left = FETCH_HEAD-only commits (how far behind we are), right = HEAD-only commits (local divergence).
    rl = _git(wsp, "rev-list", "--left-right", "--count", "FETCH_HEAD...HEAD", token=token, check=False)
    parts = rl.stdout.split()
    behind_before = int(parts[0]) if len(parts) == 2 else 0
    ahead_local = int(parts[1]) if len(parts) == 2 else 0
    if ahead_local > 0:
        # Local has commits the remote doesn't → not a fast-forward. Refuse (no auto-merge / rebase / force).
        raise RemoteSyncError(
            "cannot fast-forward — this workspace has local commits the remote doesn't have. "
            "Push them (or resolve the divergence) instead of pulling over them."
        )
    merged = _git(wsp, "merge", "--ff-only", "FETCH_HEAD", token=token, check=False)
    if merged.returncode != 0:
        raise RemoteSyncError(_redacted(f"fast-forward failed: {merged.stderr.strip()}", token))
    after = _git(wsp, "rev-parse", "HEAD", token=token).stdout.strip()
    log.info("workspace pull subject-ws=%s remote=%s ref=%s updated=%s", wsp.name, remote, branch, before != after)
    return PullResult(remote=remote, url=_display_url(url), branch=branch, head_sha=after,
                      updated=before != after, behind_before=behind_before)
