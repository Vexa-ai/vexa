"""workspace_publish.py — publish a VEXA-BORN workspace to GitHub (the counterpart of attach/swap).

A workspace that was seeded inside vexa has real git history but no home of its own. *Publish* gives
it one: create a repo under the caller's GitHub account (or an org they can create in) and push the
active workspace's current branch — full history — to it. An attached workspace (workspace_attach)
already HAS a home; publishing it is refused so the flow never shadows the user's own origin.

Credential discipline (P15 — mirrors ``POST /api/workspace/swap``): the GitHub token arrives PER
CALL in the request body, is used server-side for exactly two operations (the repo-creation API call
and the authenticated push), and is NEVER stored. The push itself goes through the shared
token-scrubbed remote mechanic (``shared.adapters.push_with_token``): a dedicated remote so the
workspace's ``origin`` is never clobbered, token on the remote URL only for the push's duration,
then scrubbed. Every error surfaced from here is token-redacted.

Re-publish is idempotent-ish: the same remote means a plain (fast-forward) push. NEVER a force push
— divergence surfaces as a clear, token-free error instead of rewriting the remote.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from shared.adapters import GitPushError, push_with_token
from shared.gitenv import scrubbed_git_env

from control_plane.workspace_attach import SEED_SLOT, _safe_subject_dir, attached_workspaces

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
# The dedicated remote publish pushes over — distinct from ``origin`` AND from the VcsPort's
# ``vexa-vcs`` remote, so neither flow ever clobbers the other's URL.
PUBLISH_REMOTE = "vexa-publish"
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class PublishError(RuntimeError):
    """A publish failed. The message is REDACTED of the access token (P15) so it is safe to surface
    in an API error body / log."""


class RepoExistsError(PublishError):
    """GitHub refused the creation because a repo with that name already exists (HTTP 422)."""


# Inject the actual GitHub call for tests (no network). Signature:
# (repo_name, private, token, org) → the repo's token-free https clone URL.
CreateRepoFn = Callable[[str, bool, str, Optional[str]], str]


@dataclass(frozen=True)
class PublishResult:
    """Outcome of one publish, the API body's shape."""

    repo_url: str    # token-free URL of the repo the workspace now lives at
    pushed_ref: str  # the branch that was pushed (the workspace's current branch)
    head_sha: str    # the workspace HEAD the remote now carries
    created: bool    # True == this call created the GitHub repo (False == pushed to an existing remote)


def _redacted(text: str, token: Optional[str]) -> str:
    return text.replace(token, "***") if token else text


def _github_create_repo(repo_name: str, private: bool, token: str, org: Optional[str]) -> str:
    """Create the repo via the GitHub REST API using the caller's PAT — ``POST /user/repos`` (or
    ``/orgs/{org}/repos`` when ``org`` is set). stdlib urllib, no extra dep (house style:
    ``shared.adapters.RuntimeHttpClient``). Returns the token-free https clone URL. All failures
    raise ``PublishError`` with the token redacted (P15); a 422 name collision raises the sharper
    ``RepoExistsError`` so the API can answer 409 with a clear, token-free message."""
    url = f"{GITHUB_API}/orgs/{org}/repos" if org else f"{GITHUB_API}/user/repos"
    body = json.dumps({"name": repo_name, "private": bool(private)}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "vexa-agent",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read() or b"{}")
            detail = str(payload.get("message") or "")
        except (ValueError, OSError):
            pass
        detail = _redacted(detail, token)
        if exc.code == 422:
            where = f"org '{org}'" if org else "your account"
            raise RepoExistsError(
                f"a repository named '{repo_name}' already exists under {where} — pick another name, "
                f"or pass its URL as remote_url to push into it"
            ) from None
        if exc.code in (401, 403):
            raise PublishError(
                f"GitHub rejected the token (HTTP {exc.code}): {detail or 'check the token and its repo scope'}"
            ) from None
        raise PublishError(f"GitHub repo creation failed (HTTP {exc.code}): {detail}".strip()) from None
    except urllib.error.URLError as exc:
        raise PublishError(f"GitHub unreachable: {_redacted(str(exc.reason), token)}") from None
    clone_url = data.get("clone_url") or data.get("html_url")
    if not clone_url:
        raise PublishError("GitHub repo creation returned no clone URL")
    return clone_url


def _git_out(ws: Path, *args: str, token: Optional[str] = None) -> str:
    """Run a read-only git query in the workspace; failures raise token-redacted PublishError."""
    proc = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True,
                          env=scrubbed_git_env())
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
    proc = subprocess.run(["git", "-C", str(wsp), "remote", "get-url", PUBLISH_REMOTE],
                          capture_output=True, text=True, env=scrubbed_git_env())
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
) -> PublishResult:
    """Publish the subject's ACTIVE workspace to GitHub: create the repo (unless ``remote_url``
    targets a pre-created/empty one) and push the current branch's FULL history.

    Vexa-born only: an ATTACHED external repo is refused (it already has an origin; use that).
    Re-publish to the same remote is a plain push — fast-forward or a clear error on divergence,
    never a force push. The ``token`` authenticates both ops and is never persisted (P15)."""
    rootp = Path(root)
    ws = _safe_subject_dir(rootp, subject)  # raises ValueError on a bad subject (API → 400)
    if not (token or "").strip():
        raise ValueError("a GitHub access token is required")  # bad input (API → 400), not a git failure
    token = token.strip()

    if not (ws / ".git").exists():
        raise PublishError("no active workspace to publish — initialize it first")

    # Vexa-born gate: the active slot carrying a repo URL means an ATTACHED external workspace.
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
    else:
        name = (repo_name or "").strip()
        if not _REPO_NAME_RE.match(name):
            raise ValueError(  # bad input (API → 400)
                "invalid repo_name — use 1-100 characters of letters, digits, '.', '_' or '-'"
            )
        # Resolved at call time (not def time) so tests can monkeypatch the module seam too.
        creator = create_repo or _github_create_repo
        remote_url = creator(name, private, token, (org or "").strip() or None)
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
    return PublishResult(repo_url=_display_url(remote_url), pushed_ref=branch,
                         head_sha=head_sha, created=created)
