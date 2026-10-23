"""home_media.py — avatars and company logos for the blocks renderer, fetched SERVER-side (#1783).

Founder, 2026-10-13: *"we want company logos here, people's avatars — this needs to be rich"*. The
browser never asks a third party for a picture: it asks agent-api, through the gateway, for an opaque
media KEY, and agent-api fetches, checks and caches the bytes. So a person's email never leaves in a
browser request, and a page an agent wrote cannot make every reader's browser call some host.

KEYS, the one shape the route accepts:

    d:<domain>                         a company's site icon: ``/apple-touch-icon.png``, then ``/favicon.ico``

NO FACES (UI decision 15g). This route used to take ``g:<md5 of an email>`` and fetch that address's
Gravatar for anyone a card named — a face looked up on the web for people who never chose one here.
It is refused now. A Vexa user's photo is kept by identity and served by ``GET
/api/people/{subject}/photo`` (``identity_media``), only to people who share a workspace or chat with
them; anyone else is initials plus their company's logo.

THE FETCH IS `asset_source.fetch_asset`, the same guarded fetcher a page's pictures use: every hop is
re-checked against the SSRF rules (no private, loopback, link-local or internal names), redirects are
re-guarded, the connection dials the address that was checked. On top of that: the answer must call
itself an image, it is capped at ``MAX_BYTES``, and SVG is refused (it can carry script).

CACHED per key on disk for ``TTL_S``, misses too (a 404 is remembered for a day), so a stream of
cards costs one fetch per person or company per week. The policy key ``external_images`` (`on`, the
default, or `off`) in `_global/POLICIES.md` turns every fetch off; the renderer then draws initials.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

from control_plane import front_page

MAX_BYTES = 256 * 1024
TTL_S = 7 * 86400
MISS_TTL_S = 86400
POLICY_KEY = "external_images"
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/x-icon", "image/vnd.microsoft.icon")
#: A public-looking host name: labels of letters, digits and inner hyphens, ending in an alphabetic
#: TLD — so an IP literal, `localhost` or an internal single-label name is never a key.
_DOM = re.compile(r"^d:(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
#: Mail providers whose domain is not the person's company — no logo is inferred from these.
FREEMAIL = frozenset({"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
                      "icloud.com", "me.com", "proton.me", "protonmail.com", "gmx.de", "gmx.net", "aol.com",
                      "yandex.ru", "mail.ru", "web.de", "hey.com", "fastmail.com"})


def avatar_key(email: Optional[str]) -> Optional[str]:
    """RETIRED (UI 15g): no face is ever looked up by address. Always None."""
    return None


def logo_key(email_or_domain: Optional[str]) -> Optional[str]:
    v = (email_or_domain or "").strip().lower()
    dom = v.split("@", 1)[1] if "@" in v else v
    if not dom or dom in FREEMAIL:
        return None
    k = f"d:{dom}"
    return k if _DOM.match(k) else None


def valid_key(key: str) -> bool:
    return bool(_DOM.match(key or ""))


def enabled(global_dir: Optional[Path]) -> bool:
    from control_plane import home  # noqa: PLC0415 — home imports this module
    raw = home.read_global(global_dir, "POLICIES.md")
    return str(front_page.front_matter(raw).get(POLICY_KEY, "on")).strip().lower() not in ("off", "false", "no", "0")


def urls_for(key: str) -> list[str]:
    if _DOM.match(key):
        dom = key[2:]
        return [f"https://{dom}/apple-touch-icon.png", f"https://{dom}/favicon.ico"]
    return []


def cache_dir() -> Path:
    d = Path(os.environ.get("VEXA_HOME_MEDIA_DIR") or "/tmp/vexa-home-media")  # noqa: S108 — a cache, rebuilt on loss
    d.mkdir(parents=True, exist_ok=True)
    return d


def get(key: str, *, fetch: Optional[Callable] = None, now: Optional[float] = None,
        root: Optional[Path] = None) -> Optional[tuple[bytes, str]]:
    """``(bytes, media type)`` for a key, or ``None`` (the renderer draws initials)."""
    if not valid_key(key):
        return None
    now = now if now is not None else time.time()
    root = root or cache_dir()
    name = hashlib.sha256(key.encode()).hexdigest()
    meta_p, body_p = root / f"{name}.json", root / f"{name}.bin"
    try:
        meta = json.loads(meta_p.read_text())
        if now - float(meta.get("at", 0)) < (TTL_S if meta.get("type") else MISS_TTL_S):
            return (body_p.read_bytes(), meta["type"]) if meta.get("type") else None
    except (OSError, ValueError, KeyError):
        pass
    if fetch is None:
        from shared import asset_source  # noqa: PLC0415
        fetch = asset_source.fetch_asset
    got: Optional[tuple[bytes, str]] = None
    for url in urls_for(key):
        try:
            body, ctype, _final = fetch(url)
        except Exception:  # noqa: BLE001 — refused, missing or unreachable: try the next, then initials
            continue
        if ctype in IMAGE_TYPES and 0 < len(body) <= MAX_BYTES:
            got = (bytes(body), ctype)
            break
    try:
        if got:
            body_p.write_bytes(got[0])
        meta_p.write_text(json.dumps({"at": now, "type": got[1] if got else ""}))
    except OSError:
        pass
    return got
