"""Where a git token may travel — the one rule clone, pull and push obey.

Two questions, answered here and nowhere else:

* **Which URL may carry a token at all?** An ``https://`` URL with no userinfo of its own. ``http://``
  would put the credential on the wire in cleartext; ``ssh://`` / ``git@host:`` authenticate with a
  deploy key and have nowhere to put one; a URL that already names a user or password would end up
  carrying two credentials.
* **Which URL may receive the caller's SAVED token?** Only ``https://github.com/<path>`` on the default
  port. The saved token is a GitHub credential entered once in the terminal's token card, and nothing
  else is entitled to it. A token the person types for one call is their choice for that call and is
  embedded for any URL the first rule allows.

``embed_token`` is the only place a token is written into a URL. Both rules parse with ``urlsplit``;
a prefix test and a string split disagree with it on mixed case, stray whitespace and userinfo.

Lives in ``shared`` because the push mechanic (``shared/adapters.py``) and the control plane's clone
and pull (``control_plane/workspace_attach.py``, ``control_plane/workspace_git_sync.py``) both need it,
and ``shared`` is the module both sides import.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import SplitResult, urlsplit


def _split(url: str) -> Optional[SplitResult]:
    try:
        parts = urlsplit((url or "").strip())
        parts.port  # noqa: B018 — raises ValueError on a malformed port
    except ValueError:
        return None
    return parts


def is_https(url: str) -> bool:
    """Whether ``url`` is an ``https://`` URL — the only transport a token is ever sent over."""
    parts = _split(url)
    return bool(parts and parts.scheme.lower() == "https")


def may_carry_token(url: str) -> bool:
    """Whether a token may be embedded in ``url``: https, a host, and no userinfo of its own."""
    parts = _split(url)
    return bool(parts and parts.scheme.lower() == "https" and parts.hostname
                and parts.username is None and parts.password is None)


def saved_token_may_reach(url: str) -> bool:
    """Whether the caller's SAVED GitHub token may be sent to ``url``: only ``https://github.com/…``.

    Any other host, any other port, ``http://``, or a URL carrying userinfo gets no saved token — a
    person can still type a token for that one call in the terminal."""
    parts = _split(url)
    if not parts or not may_carry_token(url):
        return False
    return ((parts.hostname or "").lower() == "github.com" and parts.port in (None, 443)
            and parts.path.startswith("/") and len(parts.path) > 1)


def embed_token(url: str, token: Optional[str]) -> str:
    """``url`` with ``token`` as its userinfo, for one network op — or ``url`` unchanged when there is no
    token or ``url`` may not carry one (see :func:`may_carry_token`). The caller never persists the
    result: it is passed to git as an argument or reset straight after the op."""
    if not token or not may_carry_token(url):
        return url
    parts = _split(url)
    return parts._replace(netloc=f"{token}@{parts.netloc}").geturl()
