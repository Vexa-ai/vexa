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

Two consequences worth naming, because both are security properties and neither was intended when the
list was written down:

* a **local path** (``/workspaces/<someone-else>``) is not a URL shape, so it is refused — closing a
  hole where any caller could have cloned another user's workspace out of the shared store by naming
  its path;
* the token check is duplicated inside ``workspace_attach`` rather than trusted from the route, so a
  credential cannot reach ``git clone`` through the MCP, a future route, or a test that forgot.
"""
from __future__ import annotations

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
        return f"{m.group(1)}://{m.group(2)}/{m.group(3)}/{_tidy(m.group(4))}.git"
    m = _SCP.match(v)
    if m:
        return f"{m.group(1)}@{m.group(2)}:{m.group(3)}/{_tidy(m.group(4))}.git"
    m = _SSH.match(v)
    if m:
        user = f"{m.group(1)}@" if m.group(1) else ""
        return f"ssh://{user}{m.group(2)}/{m.group(3)}/{_tidy(m.group(4))}.git"
    m = _BARE.match(v)
    if m:
        return f"https://{DEFAULT_HOST}/{m.group(1)}/{_tidy(m.group(2))}.git"
    raise RepoRefError(SHAPE_SENTENCE)
