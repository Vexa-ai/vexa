"""asset_source.py — what an IMAGE ON A PAGE is made of: the media type a workspace file is served
as, the guarded fetch that brings a remote one INTO the workspace, and the index that records where
it came from.

Founder, 2026-09-06, on a customer workspace README the agent wrote: *"we want to be able images"* —
the page showed `![OeNB logo](…)` as alt text and a broken-image icon (Vexa-ai/vexa#1612). The rule
that fixes it is not "allow images", it is **where the bytes live**: a page's image is a file in the
workspace, served by the same owner- and membership-scoped read route the page itself came from. A
customer's browser must never be told to go and get a picture from a third party because a document
in their bank's workspace said so — that is a beacon, an outage we don't control, and a request we
cannot see.

So an image the agent wants on a page is FETCHED first: stored under ``assets/`` with the source URL
recorded beside it, and referenced relatively. This module is the part of that shared by every
caller — the control plane's asset routes, and (through them) the rig's ``fetch_asset``.

**THE SOURCE IS PART OF THE ASSET.** ``kg/`` prose carries where each fact came from; an image is a
fact with no room for a citation inside it, so the citation lives in ``assets/SOURCES.md`` — one row
per file, human-readable in the pages panel, and rewritten in place so a re-fetch updates rather
than duplicates. An asset with no row is an asset nobody can check.

**THE SSRF RULE IS STATED TWICE, ON PURPOSE.** ``llm/web_tools.fetch_refusal`` states it for the
WORKER image; this states it for the CONTROL-PLANE image, which ships ``shared/`` and
``control_plane/`` and deliberately not ``llm/`` (see ``core/agent/services/agent-api/Dockerfile``),
so importing that one here would be an ImportError in the only process that runs this code — the
mirror image of the reason web_tools does not import ``control_plane/model_endpoint.py``. A comment
claiming the two agree would be worth nothing, so a test asserts it instead
(``tests/test_workspace_assets.py::test_the_two_outbound_guards_agree``).
"""
from __future__ import annotations

import ipaddress
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urljoin, urlsplit

import httpx

#: Where a fetched or uploaded asset lives. One directory, so a workspace's pictures are findable
#: and the source index has one home.
ASSETS_DIR = "assets"
#: The index that records where each asset came from. Markdown, because it is read by people in the
#: same pages panel as everything else in the workspace.
SOURCES_INDEX = f"{ASSETS_DIR}/SOURCES.md"

#: Transport ceilings for ONE fetch. Deliberately the shape of llm/web_tools': a slow or enormous
#: remote costs this call, never the request's wall clock.
FETCH_TIMEOUT = 20.0
MAX_ASSET_BYTES = 25 * 1024 * 1024      # the upload ceiling (api_shared.MAX_UPLOAD_BYTES), one rule
MAX_REDIRECTS = 3                       # every hop re-checked against the guard below

_UA = {"User-Agent": "vexa-agent/0.12 (+https://vexa.ai)", "Accept": "*/*"}

#: extension → media type. A CLOSED table, not `mimetypes.guess_type`: the media type is what the
#: browser executes the bytes AS, so it is a security decision and belongs in a list somebody read.
#: Anything absent is served as an opaque download, which is the safe answer for an unknown file.
MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".avif": "image/avif", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".ogg": "audio/ogg", ".m4a": "audio/mp4",
    ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8", ".json": "application/json",
}
DEFAULT_MEDIA_TYPE = "application/octet-stream"

#: The media types a page may render as a picture. Used to decide where an upload lands
#: (``assets/`` vs ``uploads/``) and what reference is written for it.
IMAGE_TYPES = frozenset(t for t in MEDIA_TYPES.values() if t.startswith("image/"))

#: media type → the extension we store it under, when the URL gives us nothing usable.
_EXT_FOR_TYPE = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
    "image/avif": ".avif", "image/bmp": ".bmp", "image/x-icon": ".ico", "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
}


def media_type_for(path: str) -> str:
    """The media type ``path`` is served as — from its EXTENSION, and from nothing else.

    Never sniffed from the bytes: a file the agent wrote as ``.png`` and a file that happens to
    start with `<svg` must be served as what the workspace calls it, or the name in the document
    stops describing what the browser does with it."""
    ext = Path(str(path or "")).suffix.lower()
    return MEDIA_TYPES.get(ext, DEFAULT_MEDIA_TYPE)


def is_image_path(path: str) -> bool:
    """True when a page can render this file with `![…](…)`."""
    return media_type_for(path).split(";")[0].strip() in IMAGE_TYPES


# ── the outbound guard (see the module docstring: the same rule llm/web_tools states) ────────────

def _blocked_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True                     # unreadable is not the same as safe
    return bool(ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved
                or ip.is_unspecified or ip.is_multicast)


def _resolve(host: str) -> list[str]:
    """Every address ``host`` resolves to. Separate so a test can play DNS."""
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def fetch_refusal(url: str, resolve: Optional[Callable[[str], list[str]]] = None) -> Optional[str]:
    """``None`` when this URL may be fetched into a workspace, else the reason it may not — phrased
    for the CALLER, which is a model or a person who can act on it by choosing another URL."""
    resolve = resolve or _resolve
    raw = (url or "").strip()
    if not raw:
        return "fetching an asset needs a url"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return f"{raw!r} is not a URL"
    if parts.scheme.lower() not in ("http", "https"):
        return f"only http and https can be fetched into a workspace — {raw!r} is not one"
    host = (parts.hostname or "").lower()
    if not host:
        return f"{raw!r} names no host"
    if host == "localhost" or host.endswith(".localhost"):
        return f"refusing {host!r} — the open web only, never this deployment's own network"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            return (f"refusing the single-label host {host!r} — that is an internal service name, "
                    "not a public site")
        try:
            addrs = resolve(host)
        except OSError as exc:
            return f"could not resolve {host!r} ({type(exc).__name__})"
        if not addrs:
            return f"could not resolve {host!r}"
        if any(_blocked_ip(a) for a in addrs):
            return (f"refusing {host!r} — it resolves into loopback/link-local/private address "
                    "space, which is this deployment's own network and not the open web")
        return None
    if _blocked_ip(host):
        return (f"refusing {host!r} — loopback/link-local/private/reserved addresses are this "
                "deployment's own network, not the open web")
    return None


class AssetFetchError(Exception):
    """A remote asset could not be brought in. Its text is what the caller is told.

    IT ALSO CARRIES WHOSE FAULT IT WAS (Vexa-ai/vexa#1624). The reader who pressed *Fetch into the
    workspace* on a dead Wikimedia address was answered `400: … answered 404` — our own status code
    for their request, with the remote's buried in a sentence, which reads as "the button is
    broken". Three different things end up here and a caller can only say the right sentence about
    them if it can tell them apart:

    * ``refused`` — the URL never left this deployment: unparseable, not http(s), or pointed at our
      own network. The request itself is wrong, so the caller's status is a 4xx of its own;
    * ``upstream`` — the remote answered, and answered an error. ``status`` is ITS code, and that
      is the number the person needs to see;
    * ``unreachable`` — nothing answered, or what answered was unusable (transport failure, a
      redirect loop, a body over the ceiling).
    """

    def __init__(self, message: str, *, kind: str = "unreachable", status: Optional[int] = None,
                 url: str = ""):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.url = url


def fetch_asset(url: str, *, client: Optional[httpx.Client] = None,
                resolve: Optional[Callable[[str], list[str]]] = None) -> tuple[bytes, str, str]:
    """GET one remote asset → ``(content, media_type, final_url)``.

    Every redirect hop is re-checked against ``fetch_refusal`` — a redirect into 169.254.x is the
    classic way past a guard that only reads the URL it was handed. The body is capped: an asset is
    something a page shows, not something a workspace downloads."""
    target = str(url or "").strip()
    own = client is None
    cli = client or httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=False)
    try:
        for hop in range(MAX_REDIRECTS + 1):
            refusal = fetch_refusal(target, resolve)
            if refusal:
                raise AssetFetchError(refusal, kind="refused", url=target)
            try:
                with cli.stream("GET", target, headers=_UA, timeout=FETCH_TIMEOUT) as r:
                    location = r.headers.get("location")
                    if r.status_code in (301, 302, 303, 307, 308) and location and hop < MAX_REDIRECTS:
                        target = urljoin(str(r.request.url), location)
                        continue
                    if r.status_code >= 400:
                        raise AssetFetchError(f"{target} answered {r.status_code}",
                                              kind="upstream", status=r.status_code, url=target)
                    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                    final_url = str(r.url)
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > MAX_ASSET_BYTES:
                            raise AssetFetchError(
                                f"{target} is larger than {MAX_ASSET_BYTES // (1024 * 1024)}MB — "
                                "an asset is something a page shows, not a download", url=target)
            except httpx.HTTPError as exc:
                raise AssetFetchError(f"could not fetch {target}: {type(exc).__name__}: {exc}",
                                      url=target) from None
            return bytes(buf), ctype, final_url
        raise AssetFetchError(f"gave up after {MAX_REDIRECTS} redirects", url=target)
    finally:
        if own:
            cli.close()


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def asset_path_for(url: str, media_type: str = "", given: str = "") -> str:
    """Where a fetched asset is stored: the caller's path when they named one, else a slug of the
    URL's own filename under ``assets/``.

    A bare name (``logo.svg``) is taken to mean ``assets/logo.svg`` — the directory is the rule, and
    making the caller repeat it is how half of them end up somewhere else."""
    want = (given or "").strip().strip("/")
    if not want:
        tail = unquote(urlsplit(url or "").path).rsplit("/", 1)[-1]
        want = _SAFE_NAME.sub("-", tail).strip("-._") or "asset"
    elif "/" not in want:
        want = f"{ASSETS_DIR}/{want}"
    if "/" not in want:
        want = f"{ASSETS_DIR}/{want}"
    if not Path(want).suffix:
        want += _EXT_FOR_TYPE.get((media_type or "").split(";")[0].strip().lower(), "")
    return want


_ROW = re.compile(r"^\|\s*`(?P<path>[^`]+)`\s*\|")
_HEADER = (
    "# Asset sources\n"
    "\n"
    "Where every file in `assets/` came from. Written by the fetch that stored it — an asset with\n"
    "no row here is one nobody can check.\n"
    "\n"
    "| file | source | fetched |\n"
    "| --- | --- | --- |\n"
)


def record_source(existing: str, path: str, source: str, when: Optional[datetime] = None) -> str:
    """The new text of ``assets/SOURCES.md`` after ``path`` was stored from ``source``.

    Pure, and idempotent per path: a re-fetch REPLACES that file's row rather than adding a second
    one, so the index answers "where is this from" with exactly one answer."""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    origin = (source or "").strip() or "uploaded here"
    row = f"| `{path}` | {origin} | {stamp} |"
    body = (existing or "").rstrip("\n")
    if not body:
        return _HEADER + row + "\n"
    kept = [ln for ln in body.split("\n")
            if not (_ROW.match(ln) and _ROW.match(ln).group("path") == path)]
    # a file that has lost its table (hand-edited, or never had one) gets the header appended back
    # rather than a row dangling under prose
    if not any(ln.strip().startswith("| ---") for ln in kept):
        return "\n".join(kept).rstrip("\n") + "\n\n" + _HEADER + row + "\n"
    return "\n".join(kept).rstrip("\n") + "\n" + row + "\n"
