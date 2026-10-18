"""workspace_publish.py — publish a VEXA-BORN workspace to a git host (the counterpart of attach/swap).

A workspace that was seeded inside vexa has real git history but no home of its own. *Publish* gives
it one: create a repo under the caller's account (or an org/group they can create in) and push the
active workspace's current branch — full history — to it. An attached workspace (workspace_attach)
already HAS a home; publishing it is refused so the flow never shadows the user's own origin.

WHERE a repository may be created is a short list, and anything else is refused by name
(``UnsupportedHostError``) before any request is made: ``github.com`` (the default), or a host the
operator registered as GitLab in ``VEXA_GIT_PROVIDERS`` (``shared.git_provider``), where the project
is created in the named group — nested groups included — through GitLab's ``/api/v4``. Any other host
is still a valid push target: create the repository there and pass its URL as ``remote_url``.

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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from shared import git_provider
from shared.adapters import GitPushError, push_with_token
from shared.gitexec import run_git

from control_plane.repo_ref import MAX_PATH_SEGMENTS, RepoRefError, assert_fetchable
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
    """The host refused the creation because a repo with that name already exists."""


class UnsupportedHostError(PublishError):
    """Publish was asked to CREATE a repository on a host it cannot create on. Raised before any
    network request; the message names the host, the reason, and what to do instead."""


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
    for k in sorted({(token or "").strip(), git_provider.secret_of(token)} - {""}, key=len, reverse=True):
        text = text.replace(k, "***")
    return text


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is refused, not followed: the request carries the caller's token in a header, and
    following a redirect would hand it to whatever host the answer names."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_GITLAB_OPENER = urllib.request.build_opener(_NoRedirect)
_GROUP_SEG_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Inject the GitLab call for tests (no network). Signature:
# (host, repo_name, private, token, namespace) → the project's token-free https clone URL.
CreateGitLabFn = Callable[[str, str, bool, str, Optional[str]], str]


def _gitlab_call(host: str, method: str, path: str, token: str, body: Optional[dict] = None) -> dict:
    """One GitLab ``/api/v4`` call over https with the token in ``PRIVATE-TOKEN`` — never in the URL.
    Raises ``urllib.error.HTTPError`` / ``URLError`` for the caller to translate."""
    req = urllib.request.Request(
        f"https://{host}/api/v4{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"PRIVATE-TOKEN": token, "Accept": "application/json",
                 "Content-Type": "application/json", "User-Agent": "vexa-agent"},
    )
    with _GITLAB_OPENER.open(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def _gitlab_message(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read() or b"{}")
    except (ValueError, OSError):
        return ""
    msg = payload.get("message") or payload.get("error") or ""
    return json.dumps(msg) if isinstance(msg, (dict, list)) else str(msg)


def _gitlab_create_repo(host: str, repo_name: str, private: bool, token: str,
                        namespace: Optional[str]) -> str:
    """Create the project on a GitLab host with the caller's token — ``POST /api/v4/projects``, in
    ``namespace`` (a group path, nested allowed: ``group/sub``) or the token owner's own namespace.
    Returns the token-free https clone URL. Failures raise ``PublishError`` with the token redacted;
    a name already taken in that namespace raises ``RepoExistsError``."""
    secret = git_provider.secret_of(token)
    where = f"group '{namespace}'" if namespace else "your namespace"
    try:
        body: dict = {"name": repo_name, "path": repo_name,
                      "visibility": "private" if private else "public"}
        if namespace:
            ns = _gitlab_call(host, "GET", "/namespaces/" + urllib.parse.quote(namespace, safe=""), secret)
            if not ns.get("id"):
                raise PublishError(f"GitLab {where} on {host} was not found")
            body["namespace_id"] = ns["id"]
        data = _gitlab_call(host, "POST", "/projects", secret, body)
    except urllib.error.HTTPError as exc:
        detail = _redacted(_gitlab_message(exc), token)
        if exc.code in (400, 409) and "taken" in detail:
            raise RepoExistsError(
                f"a repository named '{repo_name}' already exists in {where} on {host} — pick another "
                f"name, or pass its URL as remote_url to push into it") from None
        if exc.code in (401, 403):
            raise PublishError(f"GitLab on {host} rejected the token (HTTP {exc.code}): "
                               f"{detail or 'check the token and its api scope'}") from None
        if exc.code == 404:
            raise PublishError(f"GitLab {where} on {host} was not found, or the token cannot see it") from None
        if 300 <= exc.code < 400:
            raise PublishError(f"GitLab on {host} answered with a redirect (HTTP {exc.code}); "
                               f"use the host's canonical name") from None
        raise PublishError(f"GitLab repo creation on {host} failed (HTTP {exc.code}): {detail}".strip()) from None
    except urllib.error.URLError as exc:
        raise PublishError(f"GitLab on {host} unreachable: {_redacted(str(exc.reason), token)}") from None
    clone_url = data.get("http_url_to_repo")
    if not clone_url:
        raise PublishError(f"GitLab repo creation on {host} returned no clone URL")
    return clone_url


def creation_host(host: Optional[str]) -> str:
    """The host a publish will CREATE its repository on, or ``UnsupportedHostError``.

    ``None``/empty and ``github.com`` mean GitHub. Any other value must be a host the operator
    registered as GitLab (``VEXA_GIT_PROVIDERS``) and one this server may reach (the same host gate a
    repository URL passes, ``repo_ref.assert_fetchable``). The registration is what makes it a place
    a person's token may be sent to an API, so an unregistered host is refused, never probed."""
    h = (host or "").strip().lower().rstrip(".")
    if not h or h == "github.com":
        return "github.com"
    if not re.match(r"^[a-z0-9.-]+(?::\d{1,5})?$", h):
        raise ValueError("invalid host — give a host name such as gitlab.example.com")
    try:
        assert_fetchable(f"https://{h}/x/y.git")
    except RepoRefError as exc:
        raise ValueError(exc.sentence) from None
    if git_provider.provider_for(h).kind != git_provider.GITLAB:
        raise UnsupportedHostError(
            f"Vexa can create a repository on GitHub, or on a GitLab host this deployment registered "
            f"(VEXA_GIT_PROVIDERS); {h} is neither. Create the repository on {h} yourself and publish "
            f"with its URL as remote_url.")
    return h


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
) -> PublishResult:
    """Publish a workspace to a git host: create the repo (unless ``remote_url`` targets a
    pre-created/empty one) and push the current branch's FULL history. Default target is the
    subject's ACTIVE (seed-slot) workspace; ``ws_dir`` targets ANY workspace dir the API already
    resolved for the caller (own parked slot or shared membership — the endpoint's ``_manage_dir``).

    Vexa-born only: an ATTACHED external repo is refused (it already has an origin; use that).
    Re-publish to the same remote is a plain push — fast-forward or a clear error on divergence,
    never a force push. The ``token`` authenticates both ops and is never persisted (P15).

    ``host`` picks where a NEW repository is created (:func:`creation_host`): GitHub by default, or a
    registered GitLab host, where ``org`` is a group path. Any other host raises
    ``UnsupportedHostError`` before the workspace is touched or a request is made."""
    rootp = Path(root)
    ws = Path(ws_dir) if ws_dir is not None else _safe_subject_dir(rootp, subject)  # ValueError on a bad subject (API → 400)
    target = None if (remote_url or "").strip() else creation_host(host)  # refuse before anything runs
    if not (token or "").strip():
        raise ValueError("a GitHub access token is required")  # bad input (API → 400), not a git failure
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
        if target == "github.com":
            creator = create_repo or _github_create_repo
            remote_url = creator(name, private, token, _valid_namespace(org, nested=False))
        else:
            gl_creator = create_gitlab_repo or _gitlab_create_repo
            remote_url = gl_creator(target, name, private, token, _valid_namespace(org, nested=True))
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
