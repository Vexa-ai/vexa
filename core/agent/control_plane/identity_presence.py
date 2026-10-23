"""identity_presence.py — a person's presence choice, read from IDENTITY (Vexa-ai/vexa#1783).

Whether colleagues see a coarse "active now / today" dot for a person is the person's own choice
and a fact about the PERSON, so identity keeps it (`users.data.person_settings.presence`). agent-api
asks for it per request over the internal tier it already holds:

    GET /internal/presence?ids=1,2,3     -> {"presence": {"1": true, "2": false, "3": null}}
    PUT /internal/users/{id}/settings    {"presence": false}

``null`` is "never chosen" (shown by default); an id identity does not answer for is unknown.

FAILS CLOSED. Identity unreachable, not configured, or not knowing the person: the dot is hidden.
A colleague's presence is never shown on a guess.

CHEAP. Answers are cached for ``TTL_S`` and fetched in one batch per read (`prefetch`); a failure
marks identity down for ``DOWN_S`` so a desktop with many members does not wait on one timeout each.

CARRY-OVER. Before this, the choice was a `presence:on|off` event in the home's log. A person who
never chose in identity and whose latest home event says off is written off in identity ONCE
(`switched_off`), so nobody who opted out reappears; a later choice in identity always wins.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable, Optional

logger = logging.getLogger("agent_api.identity_presence")

TTL_S = 30.0
DOWN_S = 5.0
TIMEOUT_S = 2.0
#: identity's own cap on one `/internal/presence` read (`PRESENCE_IDS_MAX` in admin-api).
BATCH_MAX = 200


class Unavailable(Exception):
    """Identity could not answer — the caller hides presence."""


class _Unknown:
    def __repr__(self):
        return "UNKNOWN"


#: Cached for a subject identity does not know — hidden, like an unreachable identity.
_UNKNOWN = _Unknown()

_lock = threading.Lock()
_config = {"base": "", "secret": ""}
_cache: dict = {}            # subject -> (expires_at, Optional[bool])
_down_until = [0.0]


def configure(settings) -> None:
    """Point at admin-api with the internal secret, from agent-api's settings (`home.build`)."""
    base = (getattr(settings, "admin_api_url", "") or "").strip().rstrip("/") if settings is not None else ""
    try:
        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
    except Exception:  # noqa: BLE001
        secret = ""
    _config.update(base=base, secret=secret or "")


def reset() -> None:
    """Forget every cached answer (tests; and after a write, for that person)."""
    with _lock:
        _cache.clear()
        _down_until[0] = 0.0


def _http(method: str, path: str, body: Optional[dict] = None) -> "tuple[int, dict]":
    """One call to identity's internal tier. ``(0, {})`` when it cannot be reached or is not
    configured — never raises."""
    base, secret = _config["base"], _config["secret"]
    if not base or not secret:
        return 0, {}
    req = urllib.request.Request(
        f"{base}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Internal-Secret": secret, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — the internal door
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (OSError, ValueError) as e:
        logger.warning("identity presence %s %s failed: %s", method, path.split("?")[0], type(e).__name__)
        return 0, {}


#: The seam tests replace (`tests/conftest.py`).
_transport: Callable[..., "tuple[int, dict]"] = _http


def prefetch(subjects: Iterable[str]) -> dict:
    """``{subject: True | False | None}`` for every subject identity answers for (``None`` = never
    chosen); a subject identity does not know is absent. One batched read for the ones not cached.
    Raises :class:`Unavailable` when identity cannot answer."""
    wanted: list = []
    for s in subjects:
        s = str(s or "").strip()
        if s and s not in wanted:
            wanted.append(s)
    now = time.monotonic()
    out: dict = {}
    with _lock:
        missing = []
        for s in wanted:
            hit = _cache.get(s)
            if hit and hit[0] > now:
                if hit[1] is not _UNKNOWN:
                    out[s] = hit[1]
            else:
                missing.append(s)
        if missing and _down_until[0] > now:
            raise Unavailable("identity was unreachable a moment ago")
    for i in range(0, len(missing), BATCH_MAX):
        chunk = missing[i:i + BATCH_MAX]
        status, body = _transport("GET", "/internal/presence?ids=" + urllib.parse.quote(",".join(chunk), safe=","))
        answer = body.get("presence") if status == 200 and isinstance(body, dict) else None
        if not isinstance(answer, dict):
            with _lock:
                _down_until[0] = time.monotonic() + DOWN_S
            raise Unavailable(f"identity answered {status}")
        with _lock:
            exp = time.monotonic() + TTL_S
            for s in chunk:
                v = answer.get(s, _UNKNOWN)
                v = v if v is None or isinstance(v, bool) else _UNKNOWN
                _cache[s] = (exp, v)
                if v is not _UNKNOWN:
                    out[s] = v
    return out



def choice(subject: str):
    """One person's choice: True · False · None (never chosen). Raises :class:`Unavailable` when
    identity cannot answer, and ``KeyError`` when it does not know the person."""
    got = prefetch([subject])
    return got[str(subject).strip()]


def set_choice(subject: str, visible: bool) -> bool:
    """Write the person's choice to identity. True when identity stored it."""
    who = str(subject or "").strip()
    if not who:
        return False
    status, _ = _transport("PUT", f"/internal/users/{urllib.parse.quote(who, safe='')}/settings",
                           {"presence": bool(visible)})
    with _lock:
        if status == 200:
            _cache[who] = (time.monotonic() + TTL_S, bool(visible))
        else:
            _cache.pop(who, None)
    return status == 200


def switched_off(subject: str, legacy_off: Callable[[], bool]) -> bool:
    """Is ``subject``'s presence hidden from colleagues? Their own choice in identity; when they never
    chose, the old home event (``legacy_off``), carried over to identity once. Every failure hides."""
    who = str(subject or "").strip()
    try:
        value = choice(who)
    except (Unavailable, KeyError):
        return True
    if value is not None:
        return value is False
    try:
        off = bool(legacy_off())
    except Exception:  # noqa: BLE001 — a log nobody can read hides, it never shows
        return True
    if off:
        if not set_choice(who, False):
            logger.warning("presence carry-over for subject=%s not written; hidden until it is", who)
        return True
    return False
