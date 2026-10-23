"""identity_media.py — a person's photo, for the people who know them (Vexa-ai/vexa#1783, UI 15g).

Identity (admin-api) keeps every person's photo and is its one writer: an upload, the sign-in
provider's picture, or Gravatar, in that order. It does not know who shares what with whom. This
module does, so the order is:

    1. ``may_see(viewer, subject)`` — the person themself, or anyone who shares a group workspace
       (``people_directory``) or a chat with them. A call is held in a chat, and a company's people
       share its company workspace, so "a workspace, chat, call or organisation" is these two.
    2. only then ``photo(subject)`` — ``GET /internal/users/{id}/photo`` on identity's internal tier.

A viewer who may not see the person gets the same 404 as a person with no photo: the route never
says whether an account exists.

NEVER A FACE FROM THE WEB. Nothing here, or anywhere in agent-api, looks a person up by name or
address on the internet. A person who is not a user here — a CRM contact, a meeting attendee — is
drawn as initials with their company's logo; a face appears only once they join and bring their own.

FAILS CLOSED and CHEAP: identity unreachable is "no photo" (initials), answers are cached for
``TTL_S`` (misses too) in a bounded map, and a failure marks identity down for ``DOWN_S``.

HOW LONG A FACE OUTLIVES ITS REASON. Who may see is decided on every request, never cached, so a lost
shared workspace or chat hides the photo at once here. The bytes are cached ``TTL_S`` (30 s) here and
``max-age=30`` in the viewer's browser: a removed photo is gone everywhere within a minute, and at once
for its owner (whose client asks with the new version, ``?v=``).
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Callable, Iterable, Optional

logger = logging.getLogger("agent_api.identity_media")

TTL_S = 30.0
DOWN_S = 5.0
TIMEOUT_S = 3.0
CACHE_MAX = 512
#: The largest photo identity stores, plus a margin; anything bigger is not served.
MAX_BYTES = 256 * 1024
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
#: An account id as identity numbers it.
SUBJECT = re.compile(r"^[0-9]{1,12}$")
#: The route a person row points at — the one place it is spelled (``home_sources.person_of``).
ROUTE = "/api/people/{subject}/photo"

_lock = threading.Lock()
_config = {"base": "", "secret": ""}
_cache: "OrderedDict[str, tuple]" = OrderedDict()     # subject -> (expires, (bytes, type, etag) | None)
_down_until = [0.0]


def photo_url(subject: str) -> Optional[str]:
    """Where the browser asks for this account's photo, or None for something that is not one."""
    s = str(subject or "")
    return ROUTE.format(subject=s) if SUBJECT.match(s) else None


def configure(settings) -> None:
    base = (getattr(settings, "admin_api_url", "") or "").strip().rstrip("/") if settings is not None else ""
    try:
        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
    except Exception:  # noqa: BLE001
        secret = ""
    _config.update(base=base, secret=secret or "")


def reset() -> None:
    with _lock:
        _cache.clear()
        _down_until[0] = 0.0


def _http(path: str) -> "tuple[int, bytes, dict]":
    """One GET on identity's internal tier. ``(0, b"", {})`` when unreachable or unconfigured."""
    base, secret = _config["base"], _config["secret"]
    if not base or not secret:
        return 0, b"", {}
    req = urllib.request.Request(f"{base}{path}", method="GET", headers={"X-Internal-Secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — the internal door
            body = r.read(MAX_BYTES + 1)
            return r.status, body, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, b"", {}
    except (OSError, ValueError) as e:
        logger.warning("identity photo read failed: %s", type(e).__name__)
        return 0, b"", {}


#: The seam tests replace.
_transport: Callable[[str], "tuple[int, bytes, dict]"] = _http


def _is_version(got: Optional[tuple], version: str) -> bool:
    return bool(got) and str(got[2] or "").strip('"').startswith(version)


def photo(subject: str, version: str = "") -> Optional[tuple]:
    """``(bytes, content type, etag)`` for an account's photo, or None (draw the monogram).
    ``version`` (the ``?v=`` the client got from identity after a change) skips a cached answer
    that is not that version, so a new photo shows at once to the person who just set it."""
    s = str(subject or "")
    if not SUBJECT.match(s):
        return None
    version = version if re.fullmatch(r"[0-9a-f]{6,64}", version or "") else ""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(s)
        if hit and hit[0] > now and (not version or _is_version(hit[1], version)):
            _cache.move_to_end(s)
            return hit[1]
        if _down_until[0] > now:
            return None
    status, body, headers = _transport(f"/internal/users/{s}/photo")
    if status == 0:
        with _lock:
            _down_until[0] = time.monotonic() + DOWN_S
        return None
    ctype = (headers.get("content-type") or "").split(";")[0].strip().lower()
    got = None
    if status == 200 and ctype in IMAGE_TYPES and 0 < len(body) <= MAX_BYTES:
        got = (bytes(body), ctype, headers.get("etag") or "")
    with _lock:
        _cache[s] = (time.monotonic() + TTL_S, got)
        _cache.move_to_end(s)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return got


def may_see(viewer: str, subject: str, *, colleagues: Callable[[str], Iterable[dict]],
            sessions_of: Optional[Callable[[str], Iterable[dict]]] = None) -> bool:
    """May ``viewer`` see ``subject``'s photo? Themself; anyone sharing a group workspace with them
    (the @-mention directory); anyone sharing a chat (a call is held in one). Every failure: no."""
    v, s = str(viewer or ""), str(subject or "")
    if not v or not s:
        return False
    if v == s:
        return True
    try:
        if any(str(p.get("subject") or "") == s for p in colleagues(v) or []):
            return True
    except Exception:  # noqa: BLE001 — an unreadable directory names nobody
        pass
    if sessions_of is None:
        return False
    try:
        mine = {str(x.get("id")) for x in sessions_of(v) or [] if x.get("id")}
        if not mine:
            return False
        return any(str(x.get("id")) in mine for x in sessions_of(s) or [])
    except Exception:  # noqa: BLE001
        return False
