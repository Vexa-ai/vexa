"""repo_ref.py — what may be typed into a "Repository" field, and what may not.

The dialog that shipped took whatever string it was given and handed it to ``git clone``. On
2026-09-02 the string it was given was a GitHub personal access token, and git answered
``fatal: repository '<the token>' does not exist`` — which put the secret on screen, in the browser
console, and in the response body. Two failures, and this module is the first one: **an input nobody
validated**. (The second, the error text nobody scrubbed, is ``git_redaction``.)

The gate is a WHITELIST, not a blacklist, because the set of things that are a repository is small and
knowable while the set of things that are a secret is neither:

* ``https://host/owner/repo`` (``.git`` optional, ``http`` accepted for a self-hosted mirror)
* ``git@host:owner/repo.git`` — the scp-like form
* ``ssh://[user@]host[:port]/owner/repo``
* ``owner/repo`` — the bare shorthand, expanded to ``https://github.com/owner/repo.git``

Anything else is refused **before any git process starts**, which is the part that matters: a
validator that runs after the subprocess has already been told the secret has not protected anything.

The shape is not the whole gate, though it read as one until 2026-09-02: **a whitelist of shapes with
no opinion about the HOST is a server-side request forge.** ``git clone`` is a fetch this process
performs, so ``http://169.254.169.254/a/b`` (the cloud metadata service) and ``http://admin-api:8001/a/b``
(a compose neighbour) normalized cleanly and the outcome came back in the error (R-D15). ``_host_is_internal``
is the second half of the gate.

Two consequences worth naming, because both are security properties and neither was intended when the
list was written down:

* a **local path** (``/workspaces/<someone-else>``) is not a URL shape, so it is refused — closing a
  hole where any caller could have cloned another user's workspace out of the shared store by naming
  its path;
* the token check is duplicated inside ``workspace_attach`` rather than trusted from the route, so a
  credential cannot reach ``git clone`` through the MCP, a future route, or a test that forgot. The
  host check is duplicated the same way, for the same reason (``assert_public_host``).
"""
from __future__ import annotations

import ipaddress
import re
from typing import Optional

from shared.git_redaction import TOKEN_PREFIXES, looks_like_token

#: The one sentence a person sees when they paste a credential where a repository goes. It says what
#: they did, what to do instead, and where the other thing lives — a refusal that only says "invalid"
#: teaches nothing and gets retried verbatim.
TOKEN_SENTENCE = ("That looks like a token, not a repository. Paste the repository URL here; "
                  "a saved token goes in the token card.")
#: …and when it is simply not a repository reference. Saying "that looks like a token" about `foo bar`
#: would be a guess, and a wrong guess about a secret is how people learn to ignore the warning.
SHAPE_SENTENCE = ("That is not a repository. Use https://github.com/owner/repo, "
                  "git@github.com:owner/repo.git, or owner/repo.")
#: …and when the shape is right and the HOST is somewhere only this server can reach.
HOST_SENTENCE = ("That host is not reachable as a repository. Use a public or company git host, "
                 "not an address inside the deployment.")
#: …and for the OTHER field beside it, which was never validated at all.
REF_SENTENCE = "That is not a branch, tag or commit. Use a name like `main` or a commit id."

DEFAULT_HOST = "github.com"

_SEG = r"[A-Za-z0-9._-]+"                       # one path segment of an owner or repo name
_HOST = r"[A-Za-z0-9.-]+(?::\d{1,5})?"
_HTTP = re.compile(rf"^(https?)://({_HOST})/({_SEG})/({_SEG}?)/?$")
_SCP = re.compile(rf"^([A-Za-z0-9._-]+)@([A-Za-z0-9.-]+):/?({_SEG})/({_SEG}?)/?$")
_SSH = re.compile(rf"^ssh://(?:([A-Za-z0-9._-]+)@)?({_HOST})/({_SEG})/({_SEG}?)/?$")
_BARE = re.compile(rf"^({_SEG})/({_SEG})$")


class RepoRefError(ValueError):
    """A repository field that cannot be one. ``sentence`` is the text a person is shown; the raw
    value is NEVER carried on the exception, because the whole point may be that it is a secret."""

    def __init__(self, sentence: str, *, kind: str = "shape"):
        super().__init__(sentence)
        self.sentence = sentence
        self.kind = kind        # 'token' | 'shape' — the caller may want to log which, never what


def _tidy(name: str) -> str:
    return re.sub(r"\.git$", "", name)


def _host_is_internal(host: str) -> bool:
    """Is this host somewhere only the SERVER can reach — i.e. would cloning it be a request the
    caller could not have made themselves?

    ``git clone`` is a fetch performed by this process, so a shape whitelist that accepts any host
    turns the attach dialog into a server-side request forge: ``http://169.254.169.254/a/b`` is the
    cloud metadata service and ``http://admin-api:8001/a/b`` is a compose neighbour, and both
    normalize cleanly today with the outcome readable in the (token-redacted) error (R-D15).

    Two rules, and the second is the one that catches a service name:

    * a literal IP that is loopback, link-local, private, reserved, multicast or unspecified;
    * a BARE LABEL — a name with no dot. Every public and company git host is fully qualified;
      an unqualified name resolves only inside the deployment's own network, which is the whole
      class ``admin-api``, ``redis`` and ``meeting-api`` belong to. ``localhost`` included.

    A DOTTED name is left alone even when it is obviously internal (``git.internal:8080`` is an
    accepted self-hosted mirror in this codebase's own fixtures): a name with a dot is one somebody
    configured, and refusing it would break the self-host case to close nothing the two rules above
    leave open — the compose neighbours all answer to bare labels."""
    h = (host or "").strip().rstrip(".").lower()
    h = h.rsplit(":", 1)[0] if re.search(r":\d{1,5}$", h) else h
    if not h:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return "." not in h               # a bare label: only resolvable inside the deployment
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)


def _checked_host(host: str) -> str:
    if _host_is_internal(host):
        raise RepoRefError(HOST_SENTENCE, kind="host")
    return host


#: A ref git will read as a REF and not as an option: starts alphanumeric (so never `-`), and none
#: of the characters `git check-ref-format` rejects anyway.
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


def valid_ref(raw: Optional[str]) -> str:
    """The branch/tag/commit in ``raw``, or ``RepoRefError`` — the field beside "Repository".

    It was validated NOWHERE (``normalize`` covers only the URL), so a value beginning with ``-``
    reached ``git checkout`` and was consumed as an OPTION: ``--detach`` detached HEAD and exited 0,
    and the family it belongs to is not one worth enumerating (R-E14).

    **Not** an ``--`` end-of-options guard, which is the obvious fix and the wrong one here:
    ``git checkout -- <x>`` means "restore the PATH x from the index", so inserting it would stop
    every ordinary branch checkout from working. ``git clone`` is a different grammar
    (``[<options>] [--] <repo> [<dir>]``) and does take ``--``; it has one now."""
    v = str(raw or "").strip()
    if not v:
        return ""
    if not _REF_RE.match(v) or ".." in v or v.endswith((".lock", "/", ".")):
        raise RepoRefError(REF_SENTENCE, kind="ref")
    return v


def assert_public_host(raw: Optional[str]) -> None:
    """Refuse a deployment-internal HOST, wherever the call came from — ``assert_not_credential``'s
    sibling, and narrower than :func:`normalize` in the same way and for the same reason: it says
    nothing about a value that is not URL-shaped (a local bare repo in a fixture), only that a value
    which IS a URL may not point at something only this server can reach."""
    v = (raw or "").strip()
    if not v:
        return
    m = (re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^/@\s]+@)?([^/\s]+)", v)
         or re.match(r"^[A-Za-z0-9._-]+@([A-Za-z0-9.-]+):", v))
    if m and _host_is_internal(m.group(1)):
        raise RepoRefError(HOST_SENTENCE, kind="host")


def assert_not_credential(raw: Optional[str]) -> None:
    """Refuse a credential-shaped repository, wherever the call came from. Deliberately NARROWER than
    :func:`normalize` so it can sit inside ``workspace_attach`` — which is also reached by tests and
    fixtures cloning local paths — while still making it impossible for a secret to reach ``git``."""
    v = (raw or "").strip()
    if not v:
        return
    if looks_like_token(v) or v.startswith(TOKEN_PREFIXES):
        raise RepoRefError(TOKEN_SENTENCE, kind="token")


def normalize(raw: Optional[str]) -> Optional[str]:
    """The full gate for a value a PERSON typed: return the canonical URL, or raise ``RepoRefError``.

    ``None``/empty passes through as ``None`` — that is "swap back to what was here", not a repository.
    """
    v = (raw or "").strip()
    if not v:
        return None
    assert_not_credential(v)
    # A credential is not always prefix-shaped, and git's own "authenticated clone URL" carries one as
    # plain USERINFO — which is how a PAT most often arrives in a repository field: pasted into a URL
    # someone copied out of a tutorial. `ssh://git@host/...` is the legitimate case and must survive,
    # so the discriminator is the userinfo itself: a `user:password` pair, a token-shaped run, or
    # anything long enough to be opaque. A real ssh user ("git", "ubuntu") is none of those.
    m = re.match(r"^[a-z]+://([^/\s@]+)@", v)
    if m:
        userinfo = m.group(1)
        if ":" in userinfo or looks_like_token(userinfo) or len(userinfo) >= 20:
            raise RepoRefError(TOKEN_SENTENCE, kind="token")

    m = _HTTP.match(v)
    if m:
        return f"{m.group(1)}://{_checked_host(m.group(2))}/{m.group(3)}/{_tidy(m.group(4))}.git"
    m = _SCP.match(v)
    if m:
        return f"{m.group(1)}@{_checked_host(m.group(2))}:{m.group(3)}/{_tidy(m.group(4))}.git"
    m = _SSH.match(v)
    if m:
        user = f"{m.group(1)}@" if m.group(1) else ""
        return f"ssh://{user}{_checked_host(m.group(2))}/{m.group(3)}/{_tidy(m.group(4))}.git"
    m = _BARE.match(v)
    if m:
        return f"https://{DEFAULT_HOST}/{m.group(1)}/{_tidy(m.group(2))}.git"
    raise RepoRefError(SHAPE_SENTENCE)
