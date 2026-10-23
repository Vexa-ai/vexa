"""workspace_publish.py — publish a VEXA-BORN workspace to a git host (the counterpart of attach/swap).

A workspace that was seeded inside vexa has real git history but no home of its own. *Publish* gives
it one: create a repo under the caller's account (or an org/group they can create in) and push the
active workspace's current branch — full history — to it. An attached workspace (workspace_attach)
already HAS a home; publishing it is refused so the flow never shadows the user's own origin.

WHERE a repository may be created is the deployment's configured git providers
(``shared.git_provider``, ``VEXA_GIT_PROVIDERS``), picked by NAME: the built-in GitHub (the default),
a GitHub Enterprise, or a self-hosted GitLab, where the project is created in the named group —
nested groups included. The provider's adapter makes the API call; this module never builds a
provider URL. Anything else is refused by name (``UnsupportedHostError``) before any request is made.
Any other host is still a valid push target: create the repository there and pass its URL as
``remote_url``.

Credential discipline (P15 — mirrors ``POST /api/workspace/swap``): the token arrives PER CALL in
the request body (or is the person's saved token for that provider, read by the route), is used
server-side for exactly two operations (the repo-creation API call
and the authenticated push), and is NEVER stored. The push itself goes through the shared
token-scrubbed remote mechanic (``shared.adapters.push_with_token``): a dedicated remote so the
workspace's ``origin`` is never clobbered, token on the remote URL only for the push's duration,
then scrubbed. Every error surfaced from here is token-redacted.

Re-publish is idempotent-ish: the same remote means a plain (fast-forward) push. NEVER a force push
— divergence surfaces as a clear, token-free error instead of rewriting the remote.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from shared import git_provider
from shared.adapters import GitPushError, push_with_token
from shared.gitexec import run_git

from control_plane.repo_ref import MAX_PATH_SEGMENTS, RepoRefError, assert_fetchable
from control_plane.workspace_attach import SEED_SLOT, _safe_subject_dir, attached_workspaces

log = logging.getLogger(__name__)

# The dedicated remote publish pushes over — distinct from ``origin`` AND from the VcsPort's
# ``vexa-vcs`` remote, so neither flow ever clobbers the other's URL.
PUBLISH_REMOTE = "vexa-publish"
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class PublishError(RuntimeError):
    """A publish failed. The message is REDACTED of the access token (P15) so it is safe to surface
    in an API error body / log."""


class RepoExistsError(PublishError):
    """The host refused the creation because a repo with that name already exists."""


class UnsupportedHostError(PublishError):
    """Publish was asked to CREATE a repository on a host it cannot create on. Raised before any
    network request; the message names the host, the reason, and what to do instead."""


class TokenRejectedError(PublishError):
    """The provider refused the token (HTTP 401/403). ``provider`` names which configured provider."""

    def __init__(self, message: str, provider: str = ""):
        super().__init__(message)
        self.provider = provider


# Inject the default provider's create for tests (no network). Signature:
# (repo_name, private, token, org) → the repo's token-free https clone URL.
CreateRepoFn = Callable[[str, bool, str, Optional[str]], str]
# Inject any provider's create: (provider, token, repo_name, private, owner) → clone URL.
CreateWithFn = Callable[[git_provider.GitProvider, str, str, bool, Optional[str]], str]


@dataclass(frozen=True)
class PublishResult:
    """Outcome of one publish, the API body's shape."""

    repo_url: str    # token-free URL of the repo the workspace now lives at
    pushed_ref: str  # the branch that was pushed (the workspace's current branch)
    head_sha: str    # the workspace HEAD the remote now carries
    created: bool    # True == this call created the repository (False == pushed to an existing remote)
    provider: str = ""  # the configured provider the repository is on ("" when not a configured one)


def _redacted(text: str, token: Optional[str]) -> str:
    return git_provider.redact(text, token)


def _translate(exc: git_provider.GitProviderError) -> PublishError:
    """A provider's typed fault → this module's error family (messages are already token-free)."""
    if exc.fault == "repo_exists":
        return RepoExistsError(str(exc))
    if exc.fault == "token_rejected":
        return TokenRejectedError(str(exc), exc.provider)
    if exc.fault == "unsupported":
        return UnsupportedHostError(str(exc))
    return PublishError(str(exc))


def create_on(provider: git_provider.GitProvider, token: str, repo_name: str, private: bool,
              owner: Optional[str]) -> str:
    """Create ``repo_name`` on ``provider`` through its adapter; the clone URL, or ``PublishError``."""
    try:
        return provider.create_repo(token, repo_name, private, owner)
    except git_provider.GitProviderError as exc:
        raise _translate(exc) from None


def _github_create_repo(repo_name: str, private: bool, token: str, org: Optional[str]) -> str:
    """Create on the default (built-in GitHub) provider — the module seam earlier callers patch."""
    return create_on(git_provider.registry().default(), token, repo_name, private, org)


# The redirect refusal every provider call uses (``shared.git_provider``), kept under its old name.
_NoRedirect = git_provider._NoRedirect
_GITLAB_OPENER = None   # tests inject an opener here for _gitlab_create_repo

# Inject the GitLab call for tests (no network). Signature:
# (host, repo_name, private, token, namespace) → the project's token-free https clone URL.
CreateGitLabFn = Callable[[str, str, bool, str, Optional[str]], str]


def _gitlab_create_repo(host: str, repo_name: str, private: bool, token: str,
                        namespace: Optional[str]) -> str:
    """Create on the GitLab provider configured at ``host`` (or a plain one at ``https://<host>``)."""
    from shared.git_provider_gitlab import GitLabProvider
    configured = git_provider.registry().for_host(host)
    if configured is not None and configured.kind == git_provider.GITLAB and _GITLAB_OPENER is None:
        provider = configured
    else:
        provider = GitLabProvider(host, f"https://{host}", opener=_GITLAB_OPENER,
                                  ca_file=configured.ca_file if configured else None)
    return create_on(provider, token, repo_name, private, namespace)


def _unsupported(what: str) -> UnsupportedHostError:
    able = [p for p in git_provider.registry().all() if p.can_create]
    names = ", ".join(f"{p.name} ({p.label})" if p.label != p.name else p.name for p in able)
    return UnsupportedHostError(
        f"Vexa can create a repository on this deployment's git providers: {names}; {what} is not one "
        f"of them. Create the repository there yourself and publish with its URL as remote_url.")


def creation_host(host: Optional[str]) -> str:
    """The host a publish will CREATE its repository on, or ``UnsupportedHostError``.

    ``None``/empty means the default provider. Any other value must be the host of a configured
    provider that can create (``VEXA_GIT_PROVIDERS``) and one this server may reach (the same host
    gate a repository URL passes, ``repo_ref.assert_fetchable``). The configuration is what makes it
    a place a person's token may be sent to an API, so an unconfigured host is refused, never probed."""
    default = git_provider.registry().default()
    h = (host or "").strip().lower().rstrip(".")
    if not h or h == default.netloc():
        return default.netloc()
    if not re.match(r"^[a-z0-9.-]+(?::\d{1,5})?$", h):
        raise ValueError("invalid host — give a host name such as gitlab.example.com")
    try:
        assert_fetchable(f"https://{h}/x/y.git")
    except RepoRefError as exc:
        raise ValueError(exc.sentence) from None
    provider = git_provider.registry().for_host(h)
    if provider is None or not provider.can_create:
        raise _unsupported(h)
    return provider.netloc()


def resolve_provider(provider: Optional[str] = None, host: Optional[str] = None) -> git_provider.GitProvider:
    """The configured provider a publish creates on: by ``provider`` name, else by ``host``
    (:func:`creation_host`), else the default. ``UnsupportedHostError`` / ``ValueError`` otherwise."""
    reg = git_provider.registry()
    name = (provider or "").strip()
    if name:
        p = reg.by_name(name)
        if p is None or not p.can_create:
            raise _unsupported(f"'{name}'")
        return p
    if (host or "").strip():
        return reg.for_host(creation_host(host)) or reg.default()
    return reg.default()


def _valid_namespace(org: Optional[str], *, nested: bool) -> Optional[str]:
    """``org`` as a GitHub org (one name) or a GitLab group path (``group/sub``) — or ``ValueError``."""
    v = (org or "").strip().strip("/")
    if not v:
        return None
    segs = v.split("/")
    if (not nested and len(segs) != 1) or len(segs) > MAX_PATH_SEGMENTS - 1 or any(
            not _GROUP_SEG_RE.match(seg) or set(seg) == {"."} for seg in segs):
        raise ValueError("invalid org — a GitHub org name, or a GitLab group path like group/subgroup")
    return v


_GROUP_SEG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _git_out(ws: Path, *args: str, token: Optional[str] = None) -> str:
    """Run a read-only git query in the workspace; failures raise token-redacted PublishError."""
    proc = run_git(ws, *args)
    if proc.returncode != 0:
        raise PublishError(_redacted(f"git {' '.join(args)} failed: {proc.stderr.strip()}", token))
    return proc.stdout.strip()


def _display_url(remote_url: str) -> str:
    """The human URL of the repo — the clone URL without a trailing ``.git``."""
    return re.sub(r"\.git$", "", remote_url)


# Credentials embedded in an http(s) URL (``proto://user[:token]@host/…``) — stripped defensively
# before a remote URL is ever returned to a client. push_with_token already scrubs the remote back to
# the token-free URL after every push (P15); this is belt-and-braces for the read path.
_URL_CREDENTIAL_RE = re.compile(r"^([a-z][a-z0-9+.-]*://)[^/@]+@", re.IGNORECASE)


def published_remote_url(ws: str | Path) -> Optional[str]:
    """Where this workspace was published — the ``vexa-publish`` remote's token-free human URL, or
    ``None`` when it was never published (no remote / not a git repo). Read-only and quiet: this is
    a state probe, not an operation, so nothing raises. Any credential somehow present in the stored
    URL is stripped before returning (P15)."""
    wsp = Path(ws)
    if not (wsp / ".git").exists():
        return None
    proc = run_git(wsp, "remote", "get-url", PUBLISH_REMOTE)
    url = proc.stdout.strip()
    if proc.returncode != 0 or not url:
        return None
    return _display_url(_URL_CREDENTIAL_RE.sub(r"\1", url))


def publish_workspace(
    root: str | Path,
    subject: str,
    *,
    token: str,
    repo_name: Optional[str] = None,
    private: bool = True,
    org: Optional[str] = None,
    remote_url: Optional[str] = None,
    create_repo: Optional[CreateRepoFn] = None,
    ws_dir: Optional[Path] = None,
    host: Optional[str] = None,
    create_gitlab_repo: Optional[CreateGitLabFn] = None,
    provider: Optional[str] = None,
    create_with: Optional[CreateWithFn] = None,
) -> PublishResult:
    """Publish a workspace to a git host: create the repo (unless ``remote_url`` targets a
    pre-created/empty one) and push the current branch's FULL history. Default target is the
    subject's ACTIVE (seed-slot) workspace; ``ws_dir`` targets ANY workspace dir the API already
    resolved for the caller (own parked slot or shared membership — the endpoint's ``_manage_dir``).

    Vexa-born only: an ATTACHED external repo is refused (it already has an origin; use that).
    Re-publish to the same remote is a plain push — fast-forward or a clear error on divergence,
    never a force push. The ``token`` authenticates both ops and is never persisted (P15).

    ``provider`` (a configured provider's name) or, as before, ``host`` picks where a NEW repository
    is created (:func:`resolve_provider`): the default provider when neither is given. On a GitLab,
    ``org`` is a group path (nested allowed). Anything not configured raises
    ``UnsupportedHostError`` before the workspace is touched or a request is made."""
    rootp = Path(root)
    ws = Path(ws_dir) if ws_dir is not None else _safe_subject_dir(rootp, subject)  # ValueError on a bad subject (API → 400)
    target = None if (remote_url or "").strip() else resolve_provider(provider, host)  # refuse before anything runs
    if not (token or "").strip():
        raise ValueError("an access token is required")  # bad input (API → 400), not a git failure
    token = token.strip()

    if not (ws / ".git").exists():
        raise PublishError("no workspace to publish — initialize it first")

    # Vexa-born gate. Explicit target: an `origin` remote means an ATTACHED external clone — its home
    # is that repo. Legacy seed-slot path: the active slot carrying a repo URL means the same thing.
    if ws_dir is not None:
        origin = run_git(ws, "remote", "get-url", "origin")
        if origin.returncode == 0 and origin.stdout.strip():
            raise PublishError(
                "this workspace is attached from an external repo — it already has a home; "
                "push to that repo instead (publish is for vexa-born workspaces)"
            )
    else:
        state = attached_workspaces(rootp, subject)
        active = state.get("active")
        if active not in (None, SEED_SLOT) and state.get("slots", {}).get(active, {}).get("repo"):
            raise PublishError(
                "the active workspace is attached from an external repo — it already has a home; "
                "push to that repo instead (publish is for vexa-born workspaces)"
            )

    # The branch to push: the workspace's current branch (full history rides along with it).
    branch = _git_out(ws, "rev-parse", "--abbrev-ref", "HEAD", token=token)
    if not branch or branch == "HEAD":
        raise PublishError("workspace is on a detached HEAD — check out a branch to publish")
    _git_out(ws, "rev-parse", "--verify", "HEAD", token=token)  # at least one commit, or fail loud

    created = False
    if remote_url:
        remote_url = remote_url.strip()
        # A caller-supplied push target is the same instruction-to-this-server as a clone source: it
        # carries the caller's PAT to whatever host it names. RepoRefError is a ValueError → API 400.
        assert_fetchable(remote_url)
    else:
        name = (repo_name or "").strip()
        if not _REPO_NAME_RE.match(name):
            raise ValueError(  # bad input (API → 400)
                "invalid repo_name — use 1-100 characters of letters, digits, '.', '_' or '-'"
            )
        # Resolved at call time (not def time) so tests can monkeypatch the module seam too.
        owner = target.namespace(org)
        if create_with is not None:
            remote_url = create_with(target, token, name, private, owner)
        elif target.name == git_provider.DEFAULT_NAME:
            remote_url = (create_repo or _github_create_repo)(name, private, token, owner)
        elif create_gitlab_repo is not None and target.kind == git_provider.GITLAB:
            remote_url = create_gitlab_repo(target.netloc(), name, private, token, owner)
        else:
            remote_url = create_on(target, token, name, private, owner)
        created = True

    try:
        head_sha = push_with_token(ws, remote_url, branch, token, remote=PUBLISH_REMOTE)
    except GitPushError as exc:  # message already token-redacted (P15)
        msg = str(exc)
        if "non-fast-forward" in msg or "fetch first" in msg or "rejected" in msg:
            raise PublishError(
                f"push rejected — the remote has history this workspace doesn't (no force push, ever): {msg}"
            ) from None
        raise PublishError(f"git push failed: {msg}") from None

    log.info("workspace publish subject=%s remote=%s ref=%s created=%s",
             subject, remote_url, branch, created)  # metadata only — never the token (P15)
    on = git_provider.for_url(remote_url)
    return PublishResult(repo_url=_display_url(remote_url), pushed_ref=branch,
                         head_sha=head_sha, created=created, provider=on.name if on else "")
