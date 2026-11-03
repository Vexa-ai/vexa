"""meeting_url_redaction.py — what a meeting link may look like once it leaves the request.

A meeting link is a credential: Zoom's ``pwd``/``password``, Teams' ``p``, a Jitsi ``jwt`` and Zoom's
host ``zak`` each let whoever reads the line into the call. Logs ship off-box, error strings are
stored and sent in webhooks, so every log line or message that names a meeting URL names it through
``redact_url`` / ``redact_urls_in``: scheme, host and path stay (the meeting id lives there), the
VALUE of every secret-named query or fragment parameter becomes ``***``, and credentials in the
authority become ``***@``.

``strip_host_tokens`` is the other rule: a HOST-ONLY token (Zoom's ``zak``, which starts the meeting
as its host) is never stored at all. The attendee passcode stays, because a stored join link must
still join.

Pure string work, never a URL parser: the TypeScript twin (``@vexa/join``
``src/shared/url-redaction.ts``) must produce the same bytes, and both are held to one vector table
(``meeting-url-redaction-vectors.json``). This file is vendored byte-identically into every image
that needs it (gate:fact-parity, fact ``meeting-url-redaction``); edit the meeting-api copy and copy
it verbatim.
"""
from __future__ import annotations

import re

_SECRET_NAMES = frozenset({"pwd", "password", "passcode", "p", "tk", "zak", "jwt", "token", "sig",
                           "signature"})
_SECRET_PARTS = ("key", "secret", "token", "password", "passcode")
#: Parameters that make the bearer the meeting's HOST. Never persisted.
HOST_ONLY_PARAMS = frozenset({"zak"})
_MASK = "***"
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'`]+")
_TRAILING = ".,;:)!?"


def _param_name(name: str) -> str:
    """The parameter's name, lower-cased; ``&amp;`` (a URL inside HTML) separates like ``&``."""
    n = name.lower()
    return n[4:] if n.startswith("amp;") else n


def _is_secret_name(name: str) -> bool:
    n = _param_name(name)
    return n in _SECRET_NAMES or any(part in n for part in _SECRET_PARTS)


def _mask_params(params: str) -> str:
    out = []
    for piece in params.split("&"):
        name, eq, value = piece.partition("=")
        out.append(f"{name}={_MASK}" if eq and value and _is_secret_name(name) else piece)
    return "&".join(out)


def _drop_params(params: str) -> str:
    return "&".join(piece for piece in params.split("&")
                    if _param_name(piece.partition("=")[0]) not in HOST_ONLY_PARAMS)


def _mask_authority(base: str) -> str:
    sep = base.find("://")
    if sep < 0:
        return base
    start = sep + 3
    slash = base.find("/", start)
    end = len(base) if slash < 0 else slash
    authority = base[start:end]
    at = authority.rfind("@")
    if at < 0:
        return base
    return f"{base[:start]}{_MASK}{authority[at:]}{base[end:]}"


def _split(url: str) -> tuple[str, "str | None", "str | None"]:
    before_hash, hashed, fragment = url.partition("#")
    base, queried, query = before_hash.partition("?")
    return base, (query if queried else None), (fragment if hashed else None)


def redact_url(url: object) -> str:
    """One URL with every secret parameter value masked; host, path and meeting id kept."""
    base, query, fragment = _split("" if url is None else str(url))
    out = _mask_authority(base)
    if query is not None:
        out += "?" + _mask_params(query)
    if fragment is not None:
        out += "#" + _mask_params(fragment)
    return out


def strip_host_tokens(url: object) -> str:
    """The URL without its host-only parameters (``zak``); everything else byte-for-byte."""
    if url is None:
        return ""
    base, query, fragment = _split(str(url))
    out = base
    if query is not None:
        kept = _drop_params(query)
        if kept:
            out += "?" + kept
    if fragment is not None:
        kept = _drop_params(fragment)
        if kept:
            out += "#" + kept
    return out


def _each_url(text: object, rewrite) -> str:
    def one(match: "re.Match[str]") -> str:
        found = match.group(0)
        end = len(found)
        while end > 0 and found[end - 1] in _TRAILING:
            end -= 1
        return rewrite(found[:end]) + found[end:]
    return _URL_IN_TEXT.sub(one, "" if text is None else str(text))


def redact_urls_in(text: object) -> str:
    """Every http(s) URL inside free text (an error message, a log line) redacted in place."""
    return _each_url(text, redact_url)


def strip_host_tokens_in(text: object) -> str:
    """Every http(s) URL inside free text with its host-only parameters removed."""
    return _each_url(text, strip_host_tokens)
