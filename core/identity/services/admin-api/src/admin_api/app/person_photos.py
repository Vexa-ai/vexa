"""person_photos.py — a person's photo, kept by identity, the one writer (Vexa-ai/vexa#1783, UI 15g).

WHERE A PERSON'S FACE COMES FROM, in order, and nowhere else:

    upload     the person chose it themselves (Settings → Your photo, or "Change photo" on their avatar)
    provider   the picture their sign-in provider holds for them — fetched BY IDENTITY at sign-in from
               the provider (Google / OIDC ``picture`` URL; Microsoft Graph with the sign-in's own
               token, accepted only when Graph says the token is the account bound to this person),
               stored, re-checked on sign-in
    gravatar   their Gravatar, only when one exists (``d=404``), checked at sign-in and cached
    (none)     the client draws a deterministic monogram and colour

The first one present wins. Removing the upload falls back to the next.

NO CALLER HANDS IDENTITY ANOTHER PERSON'S PICTURE. The internal sign-in door takes a URL or a token,
never bytes: identity fetches the picture itself, through the outbound-URL guard. The only bytes that
come from outside are the person's own upload, with the person's own credential.

NO METADATA SURVIVES. Every stored image — upload, provider, Gravatar — is decoded and re-encoded
(``clean``): EXIF (GPS included), XMP, ICC, PNG text chunks, comments are gone, and only the pixels
remain. A phone photo never tells colleagues where it was taken.

NEVER A FACE FROM THE WEB FOR ANYONE ELSE. The only outbound fetches are made for an account, at
that account's own sign-in, to the provider it signed in with or to Gravatar for its own address.

The bytes live in the object store (``photo_storage.py``); the ``person_photos`` row keeps the key,
the SHA-256 and the type. This module is pure apart from the fetch and the store it is handed, so
every rule is tested without Postgres, S3 or a network (``tests/test_person_photos.py``).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import struct
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, Tuple

#: Precedence, first wins.
SOURCES: Tuple[str, ...] = ("upload", "provider", "gravatar")
#: The sign-in providers whose photo identity takes.
PROVIDERS = frozenset({"google", "microsoft", "oidc"})
#: The largest photo identity keeps, in bytes (and takes in).
MAX_BYTES = 200 * 1024
#: The largest side a photo may declare, in pixels; a stored photo is at most STORED_SIDE.
MAX_SIDE = 4096
STORED_SIDE = 1024
#: A provider photo is fetched again at sign-in when its address changed, or after this long.
PROVIDER_RECHECK_S = 7 * 86400
#: A Gravatar answer, a miss included, is reused for this long before sign-in asks again.
GRAVATAR_RECHECK_S = 7 * 86400
#: Microsoft Graph: who the token is, and their photo. Fixed hosts, never a caller's URL.
GRAPH_ME = "https://graph.microsoft.com/v1.0/me?$select=id"
GRAPH_PHOTO = "https://graph.microsoft.com/v1.0/me/photos/240x240/$value"
#: Google's pictures are served from here; a "google" URL anywhere else is not Google's picture.
GOOGLE_PICTURE_HOST = ".googleusercontent.com"

PNG, JPEG, WEBP = "image/png", "image/jpeg", "image/webp"
CONTENT_TYPES = frozenset({PNG, JPEG, WEBP})

#: ``fetch(url, headers)`` → the body, or None.
Fetch = Callable[..., Awaitable[Optional[bytes]]]


class Refused(ValueError):
    """An image identity will not keep. The message is safe to show the person."""


class Store(Protocol):
    """Where photos live. ``rows`` answers ``{source: meta}``; meta is ``content_type``, ``sha256``
    (both empty for a remembered miss), ``origin``, ``provider``, ``checked_at``, ``updated_at`` —
    never the bytes. ``put`` stores meta and, when given, the cleaned bytes; ``read`` gets them."""

    async def rows(self, user_id: int) -> Dict[str, dict]: ...
    async def put(self, user_id: int, source: str, meta: dict, body: Optional[bytes]) -> None: ...
    async def drop(self, user_id: int, source: str) -> None: ...
    async def read(self, user_id: int, source: str, meta: dict) -> Optional[bytes]: ...


# ── what an image is ─────────────────────────────────────────────────────────────────────────────
def _png_dims(b: bytes) -> Tuple[int, int]:
    if len(b) < 33 or b[12:16] != b"IHDR" or struct.unpack(">I", b[8:12])[0] != 13:
        raise Refused("that PNG has no header")
    if b"IEND" not in b[-16:]:
        raise Refused("that PNG is cut short")
    return struct.unpack(">II", b[16:24])


_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_dims(b: bytes) -> Tuple[int, int]:
    if b"\xff\xd9" not in b[-1024:]:
        raise Refused("that JPEG is cut short")
    i = 2
    while i + 4 <= len(b):
        if b[i] != 0xFF:
            raise Refused("that JPEG is not well formed")
        marker = b[i + 1]
        if marker == 0xFF:            # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack(">H", b[i + 2:i + 4])[0]
        if seg < 2:
            raise Refused("that JPEG is not well formed")
        if marker in _SOF:
            if i + 9 > len(b):
                break
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        if marker == 0xDA:            # start of scan with no frame header before it
            break
        i += 2 + seg
    raise Refused("that JPEG has no frame header")


def _webp_dims(b: bytes) -> Tuple[int, int]:
    if len(b) < 30:
        raise Refused("that WebP is cut short")
    riff = struct.unpack("<I", b[4:8])[0]
    if riff + 8 > len(b) or riff < 22:
        raise Refused("that WebP is cut short")
    chunk = b[12:16]
    if chunk == b"VP8X":
        w = 1 + int.from_bytes(b[24:27], "little")
        h = 1 + int.from_bytes(b[27:30], "little")
        return w, h
    if chunk == b"VP8 ":
        if b[23:26] != b"\x9d\x01\x2a":
            raise Refused("that WebP is not well formed")
        w, h = struct.unpack("<HH", b[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if chunk == b"VP8L":
        if b[20] != 0x2F:
            raise Refused("that WebP is not well formed")
        bits = int.from_bytes(b[21:25], "little")
        return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    raise Refused("that WebP is not well formed")


def sniff(body: bytes) -> str:
    """The image's real type (``image/png|jpeg|webp``), or :class:`Refused`. Decided by the bytes'
    structure, never by a name or a claimed type."""
    if not isinstance(body, (bytes, bytearray)) or not body:
        raise Refused("there is no image")
    body = bytes(body)
    if len(body) > MAX_BYTES:
        raise Refused(f"a photo is at most {MAX_BYTES // 1024} KB")
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        ctype, (w, h) = PNG, _png_dims(body)
    elif body.startswith(b"\xff\xd8\xff"):
        ctype, (w, h) = JPEG, _jpeg_dims(body)
    elif body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        ctype, (w, h) = WEBP, _webp_dims(body)
    else:
        raise Refused("a photo is a PNG, JPEG or WebP image")
    if not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
        raise Refused(f"a photo is at most {MAX_SIDE} pixels on a side")
    return ctype


def decode_data(text: str) -> bytes:
    """Base64 (optionally a ``data:image/…;base64,`` URL) → bytes, or :class:`Refused`."""
    raw = (text or "").strip()
    if raw.startswith("data:"):
        head, _, raw = raw.partition(",")
        if ";base64" not in head:
            raise Refused("an image is sent as base64")
    raw = "".join(raw.split())
    if len(raw) > (MAX_BYTES * 4) // 3 + 8:
        raise Refused(f"a photo is at most {MAX_BYTES // 1024} KB")
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise Refused("an image is sent as base64") from None


def clean(body: bytes) -> Tuple[bytes, str]:
    """The image re-encoded from its pixels alone, and its type — or :class:`Refused`.

    Checked by structure first (``sniff``: type, size, declared sides), then decoded by Pillow and
    written again with NOTHING but the pixels: no EXIF (so no GPS), no XMP, no ICC profile, no PNG
    text chunk, no comment. The orientation EXIF carried is applied to the pixels first, so the
    face is upright. An animated image keeps its first frame. A result over ``MAX_BYTES`` is made
    smaller; one that still does not fit is refused."""
    ctype = sniff(body)
    try:
        from PIL import Image, ImageOps  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover — the image always carries Pillow
        raise Refused("this server cannot read images") from e
    Image.MAX_IMAGE_PIXELS = MAX_SIDE * MAX_SIDE
    try:
        with Image.open(io.BytesIO(body)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(body)) as im:
            im.seek(0)
            im.load()
            img = ImageOps.exif_transpose(im) or im
            img = img.copy()
    except Refused:
        raise
    except Exception:  # noqa: BLE001 — anything Pillow cannot decode is not a photo
        raise Refused("that image could not be read") from None
    want_alpha = ctype != JPEG and (img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info)
    img = img.convert("RGBA" if want_alpha else "RGB")
    img.info = {}
    if max(img.size) > STORED_SIDE:
        img.thumbnail((STORED_SIDE, STORED_SIDE))

    def encode(im) -> bytes:
        out = io.BytesIO()
        if ctype == JPEG:
            im.save(out, format="JPEG", quality=88, optimize=True)
        elif ctype == PNG:
            im.save(out, format="PNG", optimize=True)
        else:
            im.save(out, format="WEBP", quality=88, method=4)
        return out.getvalue()

    data = encode(img)
    side = max(img.size)
    while len(data) > MAX_BYTES and side > 64:
        side = int(side * 0.7)
        smaller = img.copy()
        smaller.thumbnail((side, side))
        data = encode(smaller)
    if len(data) > MAX_BYTES:
        raise Refused(f"a photo is at most {MAX_BYTES // 1024} KB")
    return data, ctype


def _meta(body: bytes, ctype: str, *, origin: str = "", provider: str = "", now: float) -> dict:
    return {"content_type": ctype, "sha256": hashlib.sha256(body).hexdigest(),
            "origin": origin, "provider": provider, "checked_at": now, "updated_at": now}


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def has_image(meta: Optional[dict]) -> bool:
    return bool(meta) and meta.get("content_type") in CONTENT_TYPES and bool(meta.get("sha256"))


# ── which one is shown ───────────────────────────────────────────────────────────────────────────
def pick(rows: Dict[str, dict]) -> Optional[Tuple[str, dict]]:
    """The photo shown for a person: the first source in ``SOURCES`` holding an image."""
    for source in SOURCES:
        if has_image((rows or {}).get(source)):
            return source, rows[source]
    return None


def describe(rows: Dict[str, dict]) -> dict:
    """What the person is told about their own photo — never the bytes."""
    got = pick(rows)
    if not got:
        return {"photo": None, "sources": []}
    source, row = got
    out = {"source": source, "version": str(row.get("sha256") or "")[:12],
           "updated_at": row.get("updated_at")}
    if source == "provider" and row.get("provider"):
        out["provider"] = row["provider"]
    return {"photo": out, "sources": [s for s in SOURCES if has_image(rows.get(s))]}


# ── the person's own upload ──────────────────────────────────────────────────────────────────────
async def upload(store: Store, user_id: int, body: bytes, *, now: Optional[float] = None) -> dict:
    data, ctype = clean(body)
    await store.put(user_id, "upload", _meta(data, ctype, now=now or time.time()), data)
    return describe(await store.rows(user_id))


async def remove_upload(store: Store, user_id: int) -> dict:
    await store.drop(user_id, "upload")
    return describe(await store.rows(user_id))


# ── at sign-in ───────────────────────────────────────────────────────────────────────────────────
def gravatar_url(email: str) -> str:
    """Gravatar for one address: SHA-256 of the trimmed, lower-cased address; ``d=404`` so a person
    with no Gravatar is a 404, never a generated cartoon."""
    digest = hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()
    return f"https://gravatar.com/avatar/{digest}?d=404&s=256"


def _picture_url_ok(provider: str, url: str) -> bool:
    if not url.startswith("https://") or len(url) > 2048:
        return False
    if provider == "google":
        from urllib.parse import urlsplit  # noqa: PLC0415
        host = (urlsplit(url).hostname or "").lower()
        return host.endswith(GOOGLE_PICTURE_HOST)
    return True


def _bound_oid(bound_subjects: Optional[dict]) -> str:
    """The Microsoft object id this account is bound to (``microsoft:<tid>:<oid>``), or ""."""
    subject = str((bound_subjects or {}).get("microsoft") or "")
    parts = subject.split(":")
    return parts[2].lower() if len(parts) == 3 and parts[0] == "microsoft" else ""


async def _microsoft_photo(token: str, bound_subjects: Optional[dict], fetch: Fetch) -> Optional[bytes]:
    """Microsoft's photo for THIS account: Graph is asked who the token is, and only when that is
    the Microsoft identity bound to the account is the photo fetched with it. The token is used for
    these two requests and never stored or logged."""
    oid = _bound_oid(bound_subjects)
    if not token or not oid:
        return None
    auth = {"Authorization": f"Bearer {token}"}
    me = await fetch(GRAPH_ME, auth)
    try:
        who = str(json.loads(me or b"{}").get("id") or "").lower()
    except (ValueError, AttributeError):
        return None
    if who != oid:
        return None
    return await fetch(GRAPH_PHOTO, auth)


async def at_signin(store: Store, user_id: int, *, email: str, provider: str = "", url: str = "",
                    access_token: str = "", bound_subjects: Optional[dict] = None, fetch: Fetch,
                    gravatar: bool = True, now: Optional[float] = None) -> dict:
    """What one sign-in teaches identity about this person's photo. Returns ``{"provider": …,
    "gravatar": …}`` naming what happened to each (``stored``, ``same``, ``removed``, ``kept``,
    ``refused``, ``none``, ``skipped``, ``miss``).

    * ``google`` / ``oidc`` with ``url`` (the provider's https picture; Google's must be on
      googleusercontent.com): identity fetches it through the outbound guard.
    * ``microsoft`` with ``access_token`` (the sign-in's own): identity asks Graph who the token is
      and fetches the photo only when that is the Microsoft identity bound to this account.
    * Re-fetched only when its source changed or ``PROVIDER_RECHECK_S`` passed. A sign-in through
      the provider that wrote the stored photo, now carrying none, removes it.
    * Gravatar is asked only when neither an upload nor a provider photo exists, only for this
      account's own address, and at most once per ``GRAVATAR_RECHECK_S`` (a miss is remembered)."""
    now = now if now is not None else time.time()
    rows = await store.rows(user_id)
    out = {"provider": "none", "gravatar": "skipped"}
    if provider and provider in PROVIDERS:
        held = rows.get("provider")
        if provider == "microsoft":
            origin = _digest("graph:" + _bound_oid(bound_subjects)) if access_token else ""
        else:
            origin = _digest(url) if url else ""
        if not origin:
            if held and held.get("provider") == provider:
                await store.drop(user_id, "provider")
                rows.pop("provider", None)
                out["provider"] = "removed"
            elif held:
                out["provider"] = "kept"
        elif held and held.get("origin") == origin and held.get("provider") == provider \
                and now - float(held.get("checked_at") or 0) < PROVIDER_RECHECK_S:
            out["provider"] = "same"
        else:
            body: Optional[bytes] = None
            if provider == "microsoft":
                body = await _microsoft_photo(access_token, bound_subjects, fetch)
            elif _picture_url_ok(provider, url):
                body = await fetch(url, None)
            try:
                if not body:
                    raise Refused("no picture")
                data, ctype = clean(body)
                meta = _meta(data, ctype, origin=origin, provider=provider, now=now)
                await store.put(user_id, "provider", meta, data)
                rows["provider"] = meta
                out["provider"] = "stored"
            except Refused:
                out["provider"] = "refused"
    if not gravatar or not email or "@" not in email:
        return out
    if has_image(rows.get("upload")) or has_image(rows.get("provider")):
        return out
    held = rows.get("gravatar")
    if held and now - float(held.get("checked_at") or 0) < GRAVATAR_RECHECK_S:
        out["gravatar"] = "same" if has_image(held) else "miss"
        return out
    origin = _digest(email.strip().lower())
    body = await fetch(gravatar_url(email), None)
    try:
        if not body:
            raise Refused("no gravatar")
        data, ctype = clean(body)
        await store.put(user_id, "gravatar", _meta(data, ctype, origin=origin, now=now), data)
        out["gravatar"] = "stored"
    except Refused:
        miss = {"content_type": "", "sha256": "", "origin": origin, "provider": "",
                "checked_at": now, "updated_at": now}
        await store.put(user_id, "gravatar", miss, None)
        out["gravatar"] = "miss"
    return out


# ── the one outbound fetch ───────────────────────────────────────────────────────────────────────
async def fetch_image(url: str, headers: Optional[dict] = None, *, transport: Any = None,
                      timeout: float = 5.0) -> Optional[bytes]:
    """GET one URL through the outbound-URL guard (``ssrf.py``: every hop checked and pinned,
    redirects included). ``None`` on a non-200, an error, or a body over ``MAX_BYTES``. ``headers``
    carries a bearer for Graph only; it is never logged."""
    import httpx

    from .ssrf import SSRFError, build_pinned_transport, validate_url

    try:
        validate_url(url, what="photo URL")
    except SSRFError:
        return None
    try:
        async with httpx.AsyncClient(transport=transport or build_pinned_transport(), timeout=timeout,
                                     follow_redirects=True, max_redirects=3) as client:
            async with client.stream("GET", url, headers={"Accept": "image/png,image/jpeg,image/webp,application/json",
                                                          **(headers or {})}) as r:
                if r.status_code != 200:
                    return None
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BYTES:
                        return None
                return bytes(buf)
    except (httpx.HTTPError, SSRFError, OSError, ValueError):
        return None
