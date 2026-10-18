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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from shared.git_redaction import redact
from shared.adapters import GitPushError, push_with_token
from shared.gitenv import transport_env
from workspaces.shared import workspace_paths as wpaths
from shared.gitexec import run_git
from shared.token_destination import embed_token

from control_plane.repo_ref import assert_fetchable
from control_plane.workspace_publish import PUBLISH_REMOTE, _URL_CREDENTIAL_RE, _display_url

log = logging.getLogger(__name__)

# A dedicated remote name the sync push points at — distinct from ``origin`` AND ``vexa-publish`` so a
# push never clobbers the persisted URL of either home remote (``push_with_token`` scrubs it token-free).
SYNC_PUSH_REMOTE = "vexa-sync"
# Preference order for the "home" remote a workspace syncs with.
_HOME_REMOTES = ("origin", PUBLISH_REMOTE)


def _push_with_pat(wsp: Path, url: str, branch: str, token: str, force: bool = False) -> str:
    """The https half of the push: the PAT rides the URL for the network op and is scrubbed after
    (``push_with_token``), and a diverged remote is reported rather than forced over."""
    try:
        return push_with_token(wsp, url, branch, token, remote=SYNC_PUSH_REMOTE, force=force)
    except GitPushError as exc:  # already token-redacted (P15)
        msg = str(exc)
        if "non-fast-forward" in msg or "fetch first" in msg or "rejected" in msg:
            raise RemoteSyncError(
                f"push rejected — the remote has commits this workspace doesn't. Pull first (no force push): {msg}"
            ) from None
        raise RemoteSyncError(f"git push failed: {msg}") from None


def _is_ssh_url(url: str) -> bool:
    """``git@host:org/repo`` / ``ssh://…`` — a home whose credential is a KEY, not a token."""
    u = (url or "").strip()
    return u.startswith("ssh://") or bool(re.match(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:", u))


class RemoteSyncError(RuntimeError):
    """A push/pull failed. The message is REDACTED of any access token (P15) so it is safe to surface."""


def _redacted(text: str, token: Optional[str]) -> str:
    """SHAPE-BASED (git_redaction), with the known token as belt to the braces. The previous version
    was the replace() alone, which redacts nothing whenever the credential reached us by a route the
    caller did not classify as a credential — which is precisely when it matters."""
    return redact(text, token)


def _git(ws: Path, *args: str, token: Optional[str] = None, check: bool = True,
         ssh_env: Optional[dict] = None, url: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run a git command in ``ws`` with a scrubbed env + prompts disabled; failures raise a token-redacted
    ``RemoteSyncError`` (unless ``check=False``, which returns the completed process for the caller to read).

    ``ssh_env`` carries ``GIT_SSH_COMMAND`` from the workspace's deploy key (``deploy_keys.ssh_env``) —
    the credential for an ``ssh://``/``git@`` home. It rides the ENVIRONMENT, never the URL, so unlike a
    PAT there is nothing to embed, nothing to redact and nothing that can be persisted by accident.

    ``url`` marks this as a NETWORK op and pins git's transport allow-list to what that remote needs —
    the home remote was written from a repository the subject supplied at attach time, so it is a
    caller-influenced URL even though it is read back out of ``.git/config``."""
    env = {"GIT_ASKPASS": "true", "GIT_TERMINAL_PROMPT": "0", **(ssh_env or {})}
    if url is not None:
        env.update(transport_env(url))
    proc = run_git(ws, *args, env=env)
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


def detach_home(ws: str | Path) -> Optional[tuple[str, str]]:
    """DROP the workspace's home remote — the exact inverse of attach, and nothing more.

    What it does: ``git remote remove <home>``. What it deliberately does NOT do: touch one file in
    the working tree, rewrite one commit, or delete anything. Detaching means *stop syncing this
    workspace to GitHub*; a person who detaches has not asked to lose the tree they are reading, and
    the tree is the whole of what they would have lost had this been implemented as "swap back to
    the parked copy" (``swap_workspace``), which is a different act with a different name.

    Returns the ``(remote, url)`` that was removed, or ``None`` when there was no home to remove —
    an already-detached workspace is a no-op, not an error, because the answer the caller wants is
    "this workspace has no GitHub home", and it is true either way."""
    wsp = Path(ws)
    home = home_remote(wsp)
    if home is None:
        return None
    remote, url = home
    _git(wsp, "remote", "remove", remote)
    return remote, _display_url(url)


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


def push_origin(ws: str | Path, *, token: Optional[str] = None, ssh_env: Optional[dict] = None,
                force: bool = False) -> PushResult:
    """Push the current branch to the workspace's home remote (origin / vexa-publish), full history,
    fast-forward only. A diverged remote fails loud (no force).

    TWO credentials, and the home URL decides: an ``ssh://``/``git@`` home pushes with the workspace's
    DEPLOY KEY (``ssh_env``) and needs no token at all; an ``https`` home uses the ``token``, which is
    NEVER persisted (P15) — attached clones keep their token-free ``origin`` intact throughout."""
    wsp = Path(ws)
    token = (token or "").strip() or None
    home = home_remote(wsp)
    if home is None:
        raise RemoteSyncError("this workspace has no GitHub home yet — publish or attach a repo first")
    remote, url = home
    # The home URL is read back out of `.git/config`, which is not ours alone to write: it passes the
    # same gate a person's repository reference does before anything is sent to it (a ValueError →
    # API 400). A path on this server is refused unless a self-host operator opted its root in.
    assert_fetchable(url)
    over_ssh = bool(ssh_env) and _is_ssh_url(url)
    if not token and not over_ssh:
        raise ValueError("a GitHub access token is required")  # bad input (API → 400)
    branch = _current_branch(wsp)
    if not branch:
        raise RemoteSyncError("workspace is on a detached HEAD — check out a branch to push")
    _git(wsp, "rev-parse", "--verify", "HEAD", token=token, ssh_env=ssh_env)  # at least one commit, or fail loud
    if over_ssh:
        # No remote is written and no credential touches the URL — the key is in the environment.
        pushed = _git(wsp, "push", "--quiet", *(["--force"] if force else []), url,
                      f"HEAD:refs/heads/{branch}", ssh_env=ssh_env, check=False, url=url)
        if pushed.returncode != 0:
            err = (pushed.stderr or "").strip()
            if "non-fast-forward" in err or "fetch first" in err or "rejected" in err:
                raise RemoteSyncError(_redacted(
                    f"push rejected — the remote has commits this workspace doesn't. Pull first (no force push): {err}", None))
            raise RemoteSyncError(_redacted(f"git push failed: {err}", None))
        head_sha = _git(wsp, "rev-parse", "HEAD", ssh_env=ssh_env).stdout.strip()
    else:
        head_sha = _push_with_pat(wsp, url, branch, token, force=force)
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
    merged: bool = False            # a divergence was reconciled with a merge commit (merge=True)
    conflicts: list = field(default_factory=list)  # the files a refused merge would have conflicted on


#: The platform's own folder in a work tree (PRD 0.13.4 D5). Its authority lives in the policy store
#: outside every tree, and the tree copy is a platform-written mirror — so a remote has no business
#: changing it, and a pull that would is refused whole (deny test 28).
POLICY_DIR = "policy"


def _refuse_remote_policy(wsp: Path, token: Optional[str]) -> None:
    """Refuse a pull whose remote side changes anything under ``policy/`` since the common ancestor.
    Nothing has been merged yet when this runs, so a refusal changes nothing in the workspace."""
    base = _git(wsp, "merge-base", "HEAD", "FETCH_HEAD", token=token, check=False).stdout.strip()
    rng = (base, "FETCH_HEAD") if base else ("HEAD", "FETCH_HEAD")
    out = _git(wsp, "diff", "--name-only", *rng, "--", POLICY_DIR, token=token, check=False)
    touched = sorted(p for p in out.stdout.split() if p)
    if touched:
        log.warning("workspace pull refused subject-ws=%s: the remote changes %s", wsp.name, touched)
        raise RemoteSyncError(
            "refused: the remote changes policy/ (" + ", ".join(touched[:5]) + "). Members, visibility "
            "and connections are changed only in Vexa, never by a sync — remove those commits from the "
            "remote, then pull again.")


def pull_origin(ws: str | Path, *, token: Optional[str] = None, ssh_env: Optional[dict] = None,
                merge: bool = False) -> PullResult:
    """Fetch the home branch and FAST-FORWARD only. No merge commit, no rebase, no force: a divergence
    (local commits the remote lacks) is reported as a conflict for the user to resolve. The ``token``
    (optional — public repos need none) is used for the fetch ONLY and NEVER persisted: we fetch from the
    authenticated URL as an argument, so nothing is written to a remote or ``.git/config`` (P15)."""
    wsp = Path(ws)
    home = home_remote(wsp)
    if home is None:
        raise RemoteSyncError("this workspace has no GitHub home yet — publish or attach a repo first")
    remote, url = home
    assert_fetchable(url)                       # read back from `.git/config` — see push_origin
    branch = _current_branch(wsp)
    if not branch:
        raise RemoteSyncError("workspace is on a detached HEAD — check out a branch to pull")
    token = (token or "").strip() or None
    if ssh_env and _is_ssh_url(url):
        token = None            # an ssh home authenticates by key; there is nothing to embed
    auth_url = embed_token(url, token)   # a credential rides only over https (token_destination)
    # Fetch from the URL directly (not a persisted remote) so the credential never lands anywhere.
    fetch = _git(wsp, "fetch", "--quiet", auth_url, branch, token=token, ssh_env=ssh_env,
                 check=False, url=url)
    if fetch.returncode != 0:
        raise RemoteSyncError(_redacted(f"fetch from {remote} failed: {fetch.stderr.strip()}", token))
    fetched = _git(wsp, "rev-parse", "FETCH_HEAD", token=token, ssh_env=ssh_env).stdout.strip()
    _refuse_remote_policy(wsp, token)
    # Record the tracking ref so status stays coherent whether or not the merge fast-forwards.
    _git(wsp, "update-ref", f"refs/remotes/{remote}/{branch}", fetched, check=False)
    before = _git(wsp, "rev-parse", "HEAD", token=token, ssh_env=ssh_env).stdout.strip()
    # left = FETCH_HEAD-only commits (how far behind we are), right = HEAD-only commits (local divergence).
    rl = _git(wsp, "rev-list", "--left-right", "--count", "FETCH_HEAD...HEAD", token=token, check=False)
    parts = rl.stdout.split()
    behind_before = int(parts[0]) if len(parts) == 2 else 0
    ahead_local = int(parts[1]) if len(parts) == 2 else 0
    if ahead_local > 0 and merge and behind_before > 0:
        # RECONCILE, ASKED FOR (Vexa-ai/vexa#1783): merge the remote in — never a rebase, never a force.
        # A conflict changes NOTHING (the merge is aborted) and names the files, so the person decides.
        m = _git(wsp, "-c", "user.name=vexa-platform", "-c", "user.email=platform@vexa.local",
                 "merge", "--no-ff", "--no-edit", "-m", f"Merge {remote}/{branch}", "FETCH_HEAD",
                 token=token, check=False)
        if m.returncode != 0:
            conflicts = sorted(p for p in _git(wsp, "diff", "--name-only", "--diff-filter=U",
                                               check=False).stdout.split() if p)
            _git(wsp, "merge", "--abort", check=False)
            return PullResult(remote=remote, url=_display_url(url), branch=branch, head_sha=before,
                              updated=False, behind_before=behind_before, merged=False,
                              conflicts=list(conflicts) or ["(merge refused)"])
        after = _git(wsp, "rev-parse", "HEAD").stdout.strip()
        log.info("workspace pull+merge subject-ws=%s remote=%s ref=%s", wsp.name, remote, branch)
        return PullResult(remote=remote, url=_display_url(url), branch=branch, head_sha=after,
                          updated=True, behind_before=behind_before, merged=True, conflicts=[])
    if ahead_local > 0 and behind_before == 0:
        return PullResult(remote=remote, url=_display_url(url), branch=branch, head_sha=before,
                          updated=False, behind_before=0)          # nothing to pull: push instead
    if ahead_local > 0:
        # Local has commits the remote doesn't → not a fast-forward. Refuse (no auto-merge / rebase / force).
        raise RemoteSyncError(
            "cannot fast-forward — this workspace has local commits the remote doesn't have. "
            "Pull with merge=true to merge the remote in (a conflict changes nothing and names the "
            "files), then push."
        )
    merged = _git(wsp, "merge", "--ff-only", "FETCH_HEAD", token=token, check=False)
    if merged.returncode != 0:
        raise RemoteSyncError(_redacted(f"fast-forward failed: {merged.stderr.strip()}", token))
    after = _git(wsp, "rev-parse", "HEAD", token=token, ssh_env=ssh_env).stdout.strip()
    log.info("workspace pull subject-ws=%s remote=%s ref=%s updated=%s", wsp.name, remote, branch, before != after)
    return PullResult(remote=remote, url=_display_url(url), branch=branch, head_sha=after,
                      updated=before != after, behind_before=behind_before)


# ── LOOK, DON'T TOUCH: the fetch-and-report half of a sync ─────────────────────────────────────────
#
# `pull_origin` fetches AND fast-forwards, so the only way an agent could learn "what is ahead, what
# is behind" was to change the branch. `fetch_status` is the other half on its own: it fetches the
# home branch into the REMOTE-TRACKING ref and nothing else — never the working tree, never the
# index, never a local branch — then reports both sides of the difference. The agent's sandbox has
# no git of its own (the workspace `.git` belongs to the platform), so this is how it looks.

#: How many commits each side lists. The counts are always exact; only the listing is capped.
STATUS_COMMIT_LIMIT = 50
#: How many uncommitted paths are named. The counts are always exact.
STATUS_PATH_LIMIT = 30

_MISSING_REMOTE_REF = re.compile(r"couldn't find remote ref|could not find remote ref", re.I)


class SyncRefused(RemoteSyncError):
    """A status look that could not happen, with a KIND an agent can act on:

    * ``no_remote``     — the workspace has no GitHub home (attach or publish one first);
    * ``detached``      — HEAD is not on a branch, so there is nothing to compare;
    * ``no_credential`` — the repository refused us (add the deploy key or save a token);
    * ``network``       — the home could not be reached; ``previous_fetch_at`` says when it last was.
    """

    def __init__(self, kind: str, message: str, *, previous_fetch_at: Optional[str] = None):
        super().__init__(message)
        self.kind = kind
        self.previous_fetch_at = previous_fetch_at


@dataclass(frozen=True)
class FetchStatus:
    remote: str
    url: str                      # token-free home URL
    branch: str
    upstream: Optional[str]       # "<remote>/<branch>", or None when the home has no such branch
    ahead: int                    # local commits the home does not have (→ workspace_push)
    behind: int                   # home commits this workspace does not have (→ workspace_pull)
    local_commits: list           # [{sha, subject, files}] newest first, capped at STATUS_COMMIT_LIMIT
    remote_commits: list          # same shape, the home's side
    uncommitted: dict             # {staged, modified, untracked, paths[], more}
    fetched_at: str               # ISO-8601 UTC of this fetch
    previous_fetch_at: Optional[str]  # the fetch before this one, if any


def _iso(ts: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _last_fetch_at(ws: Path) -> Optional[str]:
    """When this workspace last fetched — git's own marker, ``FETCH_HEAD``, which every fetch writes."""
    # through workspace_paths, so an evicted workspace is hydrated before it is looked at (WS-S3)
    st = wpaths.stat_inside(ws, ".git/FETCH_HEAD", allow=(".git",))
    return _iso(st.st_mtime) if st is not None else None


def _commits(ws: Path, rng: str, limit: int) -> list:
    """``[{sha, subject, files}]`` for ``rng`` (``a..b`` or a single rev), newest first."""
    proc = _git(ws, "log", f"-n{limit}", "--format=%x1e%H%x1f%s", "--name-only", rng, check=False)
    out = []
    for chunk in proc.stdout.split("\x1e"):
        if not chunk.strip():
            continue
        head, _, rest = chunk.partition("\n")
        sha, _, subject = head.partition("\x1f")
        out.append({"sha": sha.strip(), "subject": subject.strip(),
                    "files": sum(1 for line in rest.splitlines() if line.strip())})
    return out


def _count(ws: Path, rng: str) -> int:
    proc = _git(ws, "rev-list", "--count", rng, check=False)
    try:
        return int(proc.stdout.strip() or 0)
    except ValueError:
        return 0


def _uncommitted(ws: Path) -> dict:
    """Counts of staged / modified / untracked paths plus the first few names. Read with optional
    locks OFF, so even git's index stat-refresh is not written — this look leaves `.git` as it was."""
    proc = run_git(ws, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                   env={"GIT_OPTIONAL_LOCKS": "0"})
    staged = modified = untracked = 0
    paths: list = []
    entries = proc.stdout.split("\0") if proc.returncode == 0 else []
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        x, y, path = e[0], e[1], e[3:]
        if x in "RC":
            i += 1                      # a rename carries its source as the next entry
        if x == "?" and y == "?":
            untracked += 1
        else:
            if x not in " ?":
                staged += 1
            if y not in " ?":
                modified += 1
        paths.append(path)
    shown = paths[:STATUS_PATH_LIMIT]
    return {"staged": staged, "modified": modified, "untracked": untracked,
            "paths": shown, "more": len(paths) - len(shown)}


def fetch_status(ws: str | Path, *, token: Optional[str] = None, ssh_env: Optional[dict] = None,
                 auth_failure=None, clock=None) -> FetchStatus:
    """Fetch the home branch into ``refs/remotes/<home>/<branch>`` ONLY and report the difference.

    Unchanged by this call: the working tree, the index, every local branch and HEAD. The credential
    is the one a pull would use (``token`` over https, the deploy key's ``ssh_env`` over ssh) and is
    never persisted — the fetch is from the authenticated URL as an argument (P15). A failure is a
    :class:`SyncRefused` with a kind; ``auth_failure`` (a predicate on the redacted error) decides
    ``no_credential`` vs ``network``."""
    import time as _time
    wsp = Path(ws)
    home = home_remote(wsp)
    if home is None:
        raise SyncRefused("no_remote", "this workspace has no GitHub home yet — attach or publish a repository first")
    remote, url = home
    assert_fetchable(url)                       # read back from `.git/config` — see push_origin
    branch = _current_branch(wsp)
    if not branch:
        raise SyncRefused("detached", "the workspace is on a detached HEAD — there is no branch to compare")
    previous = _last_fetch_at(wsp)
    token = (token or "").strip() or None
    if ssh_env and _is_ssh_url(url):
        token = None
    auth_url = embed_token(url, token)
    tracking = f"refs/remotes/{remote}/{branch}"
    # A refspec into the TRACKING ref: git refuses to fetch into a checked-out branch anyway, and
    # nothing here names one. No merge, no checkout, no reset follows.
    fetch = _git(wsp, "fetch", "--quiet", "--no-tags", auth_url, f"+refs/heads/{branch}:{tracking}",
                 token=token, ssh_env=ssh_env, check=False, url=url)
    fetched_at = _iso(clock() if clock else _time.time())
    upstream: Optional[str] = f"{remote}/{branch}"
    if fetch.returncode != 0:
        err = _redacted((fetch.stderr or "").strip(), token)
        if _MISSING_REMOTE_REF.search(err):
            upstream = None             # the home has no such branch: everything here is ahead
        else:
            is_auth = auth_failure(err) if auth_failure else False
            raise SyncRefused("no_credential" if is_auth else "network",
                              f"fetch from {remote} failed: {err}", previous_fetch_at=previous)
    if upstream is None or _tracking_ref(wsp, remote, branch) is None:
        upstream = None
        ahead, behind = _count(wsp, "HEAD"), 0
        local = _commits(wsp, "HEAD", STATUS_COMMIT_LIMIT)
        theirs: list = []
    else:
        ahead, behind = _ahead_behind(wsp, tracking)
        local = _commits(wsp, f"{tracking}..HEAD", STATUS_COMMIT_LIMIT) if ahead else []
        theirs = _commits(wsp, f"HEAD..{tracking}", STATUS_COMMIT_LIMIT) if behind else []
    log.info("workspace status subject-ws=%s remote=%s ref=%s ahead=%s behind=%s",
             wsp.name, remote, branch, ahead, behind)   # metadata only (P15)
    return FetchStatus(remote=remote, url=_display_url(url), branch=branch, upstream=upstream,
                       ahead=ahead, behind=behind, local_commits=local, remote_commits=theirs,
                       uncommitted=_uncommitted(wsp), fetched_at=fetched_at, previous_fetch_at=previous)
