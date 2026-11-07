"""workspace_icon.py — a workspace's IMAGE: where it lives, what it may contain, and its default.

UI decision 15f (founder, 2026-10-15): *"workspace must have identity — an image"*; *"agent must find
logo or choose identity for workspace so that all is human beautiful"*.

WHERE IT LIVES. In the workspace, so it travels with it through every rename, share and clone:
``.vexa/icon.svg``, ``.vexa/icon.png`` or ``.vexa/icon.webp``, named by the README's front matter
``icon:``. The workspace's own commits are the one writer. A README ``icon:`` naming anything else is
ignored — the three names above are the whole set, so a page can never point the image route at a
file somewhere else in the tree.

WHAT IT MAY CONTAIN. At most ``MAX_BYTES``. PNG and WebP by their magic numbers, never by a name.
SVG is parsed and rebuilt from an ALLOWLIST of drawing elements and attributes: no script, no event
handler, no ``foreignObject``, no external ``href`` / ``xlink:href`` / ``url()`` (a ``#local`` one
stays), no ``data:`` beyond an embedded raster image, no DOCTYPE or entity. A JPEG, GIF or ICO a site
offers is kept by wrapping it in such an SVG, so the stored set stays three types. The image route
also serves it under a ``default-src 'none'; sandbox`` CSP with ``nosniff`` (the asset route's
headers), so even a scrub that missed something runs nothing.

THE DEFAULT. A workspace with no image of its own shows its bound company's logo: README front matter
``domain:`` (or ``website:``) names the company's site, and the logo comes through the existing media
proxy (``home_media``, the guarded and cached server-side fetch). With neither, the route answers 404
and the client draws the monogram and colour derived from the workspace id.

PROPOSALS. In a shared workspace an AGENT's change is a proposal: the bytes wait in
``.vexa/icon-proposal.<ext>`` with ``.vexa/icon-proposal.json`` beside them (who, when, from where),
both git-excluded — a queue, not a fact about the workspace — until the owner accepts (the bytes
become the icon, one commit) or declines (both files go). A person writing their own workspace, and
an agent writing a private one, set it directly.
"""
from __future__ import annotations

import base64
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from workspaces.shared import workspace_paths as wpaths
from workspaces.shared.workspace_id import VEXA_DIR

from control_plane import front_page, home_media

MAX_BYTES = 200 * 1024
KINDS = ("svg", "png", "webp")
ICON_PATHS = {k: f"{VEXA_DIR}/icon.{k}" for k in KINDS}
PROPOSAL_META = f"{VEXA_DIR}/icon-proposal.json"
PROPOSAL_PATHS = {k: f"{VEXA_DIR}/icon-proposal.{k}" for k in KINDS}
MEDIA_TYPE = {"svg": "image/svg+xml", "png": "image/png", "webp": "image/webp"}
README = "README.md"
_ALLOW = (VEXA_DIR,)

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


class IconRefused(ValueError):
    """The image cannot be stored. Its text is what the caller is told; ``status`` the HTTP answer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── what an image may contain ────────────────────────────────────────────────────────────────────

#: Drawing elements only. Anything else — script, foreignObject, iframe, style, animation, a link
#: element of another namespace — is dropped with its subtree.
_ELEMENTS = frozenset({
    "svg", "g", "defs", "title", "desc", "path", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "linearGradient", "radialGradient", "stop", "clipPath", "mask",
    "symbol", "use", "image", "a", "pattern",
})
_ATTRS = frozenset({
    "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "d", "points", "fill", "fill-opacity", "fill-rule", "clip-rule", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-opacity", "stroke-dasharray", "stroke-dashoffset",
    "stroke-miterlimit", "opacity", "transform", "id", "class", "offset", "stop-color",
    "stop-opacity", "gradientUnits", "gradientTransform", "fx", "fy", "spreadMethod",
    "clip-path", "mask", "clipPathUnits", "maskUnits", "patternUnits", "preserveAspectRatio",
    "font-family", "font-size", "font-weight", "text-anchor", "dominant-baseline", "letter-spacing",
    "href", "style", "version", "role", "aria-label", "aria-hidden", "focusable",
})
_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
_DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|gif|webp|x-icon|vnd\.microsoft\.icon);base64,[A-Za-z0-9+/=\s]*$", re.I)
_BAD_STYLE = re.compile(r"@import|expression\s*\(|javascript:|behavior\s*:|-moz-binding", re.I)


def _local(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


def _url_ok(value: str) -> bool:
    """Every ``url(…)`` in ``value`` names a fragment in this document."""
    return all(m.group(2).strip().startswith("#") for m in _URL.finditer(value))


def _clean_attrs(el: ET.Element, local: str) -> None:
    for key in list(el.attrib):
        ns, name = _local(key)
        val = el.attrib[key]
        low = val.strip().lower()
        keep = (ns in ("", XLINK_NS) and (name in _ATTRS or (ns == XLINK_NS and name == "href")))
        if keep and name.lower().startswith("on"):
            keep = False
        if keep and name == "href":
            keep = val.strip().startswith("#") or (local == "image" and bool(_DATA_IMAGE.match(val.strip())))
        if keep and ("javascript:" in low or "vbscript:" in low):
            keep = False
        if keep and "url(" in low and not _url_ok(val):
            keep = False
        if keep and name == "style" and _BAD_STYLE.search(val):
            keep = False
        if keep and low.startswith("data:") and name != "href":
            keep = False
        if not keep:
            del el.attrib[key]


def _scrub(el: ET.Element) -> None:
    for child in list(el):
        if not isinstance(child.tag, str):           # a comment or processing instruction
            el.remove(child)
            continue
        ns, local = _local(child.tag)
        if ns != SVG_NS or local not in _ELEMENTS:
            el.remove(child)
            continue
        _clean_attrs(child, local)
        if local == "image" and not any(_local(k)[1] == "href" for k in child.attrib):
            el.remove(child)                         # an image whose source was refused draws nothing
            continue
        _scrub(child)


def sanitize_svg(raw: bytes) -> bytes:
    """``raw`` rebuilt from the allowlist above, or ``IconRefused`` when it is not an SVG at all."""
    text = raw.decode("utf-8", errors="strict") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
        raise IconRefused("an SVG with a DOCTYPE or entity declarations is not accepted")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise IconRefused(f"not a well-formed SVG ({exc})") from None
    ns, local = _local(root.tag)
    if local != "svg" or ns != SVG_NS:
        raise IconRefused("the image must be an <svg> in the SVG namespace")
    _clean_attrs(root, "svg")
    _scrub(root)
    return ET.tostring(root, encoding="unicode").encode("utf-8")


def _kind_of(body: bytes) -> Optional[str]:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    return None


def checked(body: bytes, kind: str) -> tuple[bytes, str]:
    """``(bytes to store, kind)`` — size-capped, magic-checked, SVG sanitised; else ``IconRefused``."""
    kind = str(kind or "").strip().lower().lstrip(".")
    if kind not in KINDS:
        raise IconRefused(f"a workspace image is svg, png or webp — not {kind or 'unnamed'!r}")
    if len(body) > MAX_BYTES:
        raise IconRefused(f"a workspace image is at most {MAX_BYTES // 1024} KB — this one is "
                          f"{len(body) // 1024} KB", status=413)
    if not body:
        raise IconRefused("the image is empty")
    if kind == "svg":
        out = sanitize_svg(body)
        if len(out) > MAX_BYTES:
            raise IconRefused(f"a workspace image is at most {MAX_BYTES // 1024} KB", status=413)
        return out, "svg"
    if _kind_of(body) != kind:
        raise IconRefused(f"those bytes are not a {kind.upper()}")
    return bytes(body), kind


_WRAP_TYPES = {"image/jpeg", "image/gif", "image/x-icon", "image/vnd.microsoft.icon"}


def from_fetched(body: bytes, ctype: str) -> tuple[bytes, str]:
    """A picture fetched from a site, as something storable: PNG/WebP/SVG as they are (checked),
    JPEG/GIF/ICO wrapped in an SVG that embeds them as a data: image."""
    ctype = str(ctype or "").split(";")[0].strip().lower()
    if ctype == "image/svg+xml":
        return checked(body, "svg")
    sniff = _kind_of(body)
    if sniff:
        return checked(body, sniff)
    if ctype in _WRAP_TYPES:
        b64 = base64.b64encode(body).decode()
        svg = (f'<svg xmlns="{SVG_NS}" viewBox="0 0 64 64"><image width="64" height="64" '
               f'preserveAspectRatio="xMidYMid meet" href="data:{ctype};base64,{b64}"/></svg>')
        return checked(svg.encode(), "svg")
    raise IconRefused(f"a {ctype or 'file of unknown type'} is not a picture a workspace can show")


# ── reading and writing it ───────────────────────────────────────────────────────────────────────

def _readme(ws: Path) -> str:
    return wpaths.read_text_inside(ws, README, max_bytes=512 * 1024) or ""


def icon_path(ws: Path) -> Optional[str]:
    """The relative path of the workspace's own image, or ``None``. The README names it; the name
    must be one of the three allowed ones and the file must exist."""
    named = front_page.front_matter(_readme(ws)).get("icon", "").strip()
    named = named[2:] if named.startswith("./") else named
    if named in ICON_PATHS.values() and wpaths.is_file_inside(ws, named, allow=_ALLOW):
        return named
    return None


def read_icon(ws: Path) -> Optional[tuple[bytes, str]]:
    rel = icon_path(ws)
    if not rel:
        return None
    data = wpaths.read_bytes_inside(ws, rel, max_bytes=MAX_BYTES + 1, allow=_ALLOW)
    if data is None:
        return None
    return data, MEDIA_TYPE[rel.rsplit(".", 1)[1]]


def _set_front(text: str, key: str, value: Optional[str]) -> str:
    """``text`` with front-matter ``key`` set to ``value`` (or removed for ``None``); a README with
    no front matter gains one. Everything else in the file is left as it was."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            inner = text[3:end].strip("\n")
            head = [ln for ln in (inner.split("\n") if inner else [])
                    if not re.match(rf"^{re.escape(key)}\s*:", ln)]
            if value is not None:
                head.append(f"{key}: {value}")
            rest = text[end + 4:]
            return ("---\n" + "\n".join(head) + "\n---" + rest) if head else rest.lstrip("\n")
    if value is None:
        return text
    return f"---\n{key}: {value}\n---\n" + text


def write_icon(ws: Path, body: bytes, kind: str) -> list[str]:
    """Store checked bytes as the workspace's image and name them in the README. Returns the paths
    touched (for the caller's commit)."""
    body, kind = checked(body, kind)
    rel = ICON_PATHS[kind]
    touched = [rel, README]
    for other in ICON_PATHS.values():
        if other != rel and wpaths.unlink_inside(ws, other, allow=_ALLOW):
            touched.append(other)
    wpaths.write_bytes_inside(ws, rel, body, allow=_ALLOW)
    wpaths.write_text_inside(ws, README, _set_front(_readme(ws), "icon", rel))
    return touched


def reset_icon(ws: Path) -> list[str]:
    touched = [README]
    for rel in ICON_PATHS.values():
        if wpaths.unlink_inside(ws, rel, allow=_ALLOW):
            touched.append(rel)
    text = _readme(ws)
    if "icon" in front_page.front_matter(text):
        wpaths.write_text_inside(ws, README, _set_front(text, "icon", None))
    return touched


# ── the standard icon (README `glyph:`, founder 2026-10-18) ─────────────────────────────────────

def glyph(ws: Path, slug: str = "") -> tuple[Optional[str], Optional[str]]:
    """``(name, by)``: the stored standard icon, or ``(None, None)``."""
    from control_plane import workspace_glyph  # noqa: PLC0415
    return workspace_glyph.stored(front_page.front_matter(_readme(ws)))


def derived_glyph(ws: Path, slug: str) -> str:
    """The icon a workspace with none stored shows: what its README and slug match (no model call on
    a read), else a neutral one stable for the slug. The personal workspace wears the desk's."""
    from control_plane import workspace_glyph  # noqa: PLC0415
    if slug in ("", "personal", "desk"):
        return workspace_glyph.DESK
    text = _readme(ws)
    name, purpose = workspace_glyph.name_and_purpose(text, front_page.front_matter(text), slug)
    return workspace_glyph.match(name, purpose) or workspace_glyph.generic(slug)


def set_glyph(ws: Path, name: str, by: str) -> list[str]:
    """Store a standard icon in the README (``glyph:`` and ``glyph_by:``). A PERSON's pick also drops
    any image of the workspace's own, so what they chose is what shows. Returns the paths touched."""
    from control_plane import workspace_glyph  # noqa: PLC0415
    if not workspace_glyph.known(name):
        raise IconRefused(f"{name!r} is not one of the standard icons")
    touched = [README]
    text = _readme(ws)
    if by == "person":
        for rel in ICON_PATHS.values():
            if wpaths.unlink_inside(ws, rel, allow=_ALLOW):
                touched.append(rel)
        text = _set_front(text, "icon", None) if "icon" in front_page.front_matter(text) else text
    text = _set_front(_set_front(text, "glyph", name), "glyph_by", by)
    wpaths.write_text_inside(ws, README, text)
    return touched


def assign_glyph(ws: Path, name: str, ws_id: str, *, purpose: str = "", author: str = "vexa",
                 trailers: Iterable[str] = ()) -> Optional[str]:
    """AT CREATION: store the icon the name (and purpose) match, else a neutral one, as ``auto``, in one
    commit (closed with ``trailers``: who made it when an agent did, ``commit_trailers``). Never raises — a workspace is made with or without its icon (the image route still
    derives one). No model call here: creation stays instant; ``PUT … glyph: auto`` asks one."""
    from control_plane import workspace_glyph  # noqa: PLC0415
    from control_plane.commit_trailers import with_trailers  # noqa: PLC0415
    from shared.gitexec import run_git  # noqa: PLC0415
    try:
        pick, by = workspace_glyph.choose(name or "", purpose, ws_id)
        touched = set_glyph(ws, pick, by)
        if (ws / ".git").is_dir():
            run_git(ws, "add", "--", *touched)
            run_git(ws, "-c", f"user.name={author or 'vexa'}", "-c", "user.email=terminal@vexa.local",
                    "commit", "-q", "-m", with_trailers(f"workspace icon: {pick} ({by})", trailers), "--", *touched)
        return pick
    except Exception:  # noqa: BLE001
        return None


def company_domain(ws: Path) -> Optional[str]:
    fm = front_page.front_matter(_readme(ws))
    raw = (fm.get("domain") or fm.get("website") or fm.get("company_domain") or "").strip().lower()
    raw = re.sub(r"^https?://", "", raw).split("/", 1)[0].removeprefix("www.")
    return raw or None


def company_logo_key(ws: Path) -> Optional[str]:
    """The media-proxy key of the bound company's logo, or ``None``."""
    return home_media.logo_key(company_domain(ws) or "")


# ── proposals (shared workspace, agent author) ───────────────────────────────────────────────────

def proposal(ws: Path) -> Optional[dict]:
    raw = wpaths.read_text_inside(ws, PROPOSAL_META, max_bytes=8192, allow=_ALLOW)
    if not raw:
        return None
    try:
        meta = json.loads(raw)
    except ValueError:
        return None
    kind = meta.get("kind")
    if kind not in KINDS or not wpaths.is_file_inside(ws, PROPOSAL_PATHS[kind], allow=_ALLOW):
        return None
    return {"by": str(meta.get("by") or ""), "at": meta.get("at"), "source": str(meta.get("source") or ""),
            "kind": kind}


def read_proposal(ws: Path) -> Optional[tuple[bytes, str]]:
    p = proposal(ws)
    if not p:
        return None
    data = wpaths.read_bytes_inside(ws, PROPOSAL_PATHS[p["kind"]], max_bytes=MAX_BYTES + 1, allow=_ALLOW)
    return (data, MEDIA_TYPE[p["kind"]]) if data is not None else None


def propose(ws: Path, body: bytes, kind: str, *, by: str, source: str = "") -> dict:
    body, kind = checked(body, kind)
    drop_proposal(ws)
    for line in (PROPOSAL_META, *PROPOSAL_PATHS.values()):
        wpaths.ensure_git_exclude(ws, line)
    wpaths.write_bytes_inside(ws, PROPOSAL_PATHS[kind], body, allow=_ALLOW)
    meta = {"kind": kind, "by": by[:80], "at": time.time(), "source": source[:300]}
    wpaths.write_text_inside(ws, PROPOSAL_META, json.dumps(meta), allow=_ALLOW)
    return {k: meta[k] for k in ("by", "at", "source", "kind")}


def drop_proposal(ws: Path) -> None:
    for rel in (PROPOSAL_META, *PROPOSAL_PATHS.values()):
        wpaths.unlink_inside(ws, rel, allow=_ALLOW)


def accept_proposal(ws: Path) -> Optional[list[str]]:
    got = read_proposal(ws)
    p = proposal(ws)
    if not got or not p:
        return None
    touched = write_icon(ws, got[0], p["kind"])
    drop_proposal(ws)
    return touched


# ── finding a site's logo (the agent's default for a company or project workspace) ────────────────

class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.icons: list[tuple[int, str]] = []
        self.og: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "link" and a.get("href"):
            rel = a.get("rel", "").lower().split()
            if "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
                self.icons.append((0, a["href"]))
            elif "icon" in rel:
                svg = a.get("type", "").endswith("svg") or a["href"].lower().endswith(".svg")
                self.icons.append((1 if svg else 2, a["href"]))
        elif tag == "meta" and a.get("content") and (a.get("property") or a.get("name", "")).lower() in (
                "og:image", "twitter:image"):
            self.og = self.og or a["content"]


def find_site_logo(domain: str, *, fetch: Optional[Callable] = None) -> Optional[tuple[bytes, str, str]]:
    """``(bytes, kind, url)`` of the best logo ``domain``'s site offers, fetched under the outbound
    URL rules (``asset_source.fetch_asset``: SSRF guard on every hop, size cap) — the
    apple-touch-icon, then the page's own ``<link rel=icon>`` (SVG first), then ``/favicon.ico``,
    then ``og:image``. ``None`` when nothing usable answers."""
    if fetch is None:
        from shared import asset_source  # noqa: PLC0415
        fetch = asset_source.fetch_asset
    dom = re.sub(r"^https?://", "", str(domain or "").strip().lower()).split("/", 1)[0]
    if not home_media.logo_key(dom):
        return None
    base = f"https://{dom}/"

    def _try(url: str) -> Optional[tuple[bytes, str, str]]:
        try:
            body, ctype, _final = fetch(url)
        except Exception:  # noqa: BLE001 — refused, missing or unreachable: try the next
            return None
        if len(body) > MAX_BYTES or not str(ctype).startswith("image/"):
            return None
        try:
            stored, kind = from_fetched(bytes(body), ctype)
        except (IconRefused, UnicodeDecodeError):
            return None
        return stored, kind, url          # the address asked; `final` names the pinned IP, not the host

    got = _try(urljoin(base, "apple-touch-icon.png"))
    if got:
        return got
    links = _Links()
    try:
        html, ctype, _final = fetch(base)
        if "html" in str(ctype):
            links.feed(bytes(html[:512 * 1024]).decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        pass
    for _rank, href in sorted(links.icons, key=lambda t: t[0]):
        got = _try(urljoin(base, href))
        if got:
            return got
    got = _try(urljoin(base, "/favicon.ico"))
    if got:
        return got
    return _try(urljoin(base, links.og)) if links.og else None
