"""workspace_glyph.py — a workspace's STANDARD ICON (founder 2026-10-18: *"let's pick icons from a
large selection of standard icons at creation unless changed"*).

A workspace attribute, not a new object: README front matter ``glyph: <name>`` names one icon of the
standard outline set the client draws with (lucide, ISC), and ``glyph_by:`` says who chose it —
``auto`` (the matcher at creation), ``model`` (the small-model fallback) or ``person`` (the picker).
The list — every icon's name, the words that pick it and its strokes — is generated once for client
and server alike (``workspace_glyphs.json``, ``clients/terminal/scripts/gen-workspace-glyphs.mjs``).

THE MATCHER. Cheap and deterministic, the same as the client's ``platform/workspaceGlyphs``: the
workspace's name and purpose split into words (accents off, a plural or "-ing" off) and adjacent
pairs; every keyword hit scores 3, a weak keyword ("~club", "~company") 1; name words count twice what
purpose words do; ties go to the list's order. Nothing matched → a small model picks a name from the
list, when the deployment offers one (``home_model.complete_json``, the cheapest catalog model) and
answers with a listed name; otherwise a neutral icon stable for the workspace id.

WHERE IT SHOWS. ``GET /api/workspaces/<ws>/identity/icon`` answers it as an SVG carrying
``data-vexa-glyph="<name>"`` when the workspace has no image of its own (and, for an icon the person
chose, before the company logo), so every surface drawing the workspace's face shows it — a
workspace made before this, with nothing stored, gets the icon its README and slug match.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional
from xml.sax.saxutils import quoteattr

DATA = Path(__file__).with_name("workspace_glyphs.json")
BY = ("auto", "model", "person")
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
#: The personal workspace (My desk) wears the desk's own icon.
DESK = "briefcase"


@lru_cache(maxsize=1)
def _doc() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def names() -> list[str]:
    return [g["name"] for g in _doc()["glyphs"]]


@lru_cache(maxsize=1)
def _by_name() -> dict:
    return {g["name"]: g for g in _doc()["glyphs"]}


def known(name: str) -> bool:
    return bool(name) and name in _by_name()


def stem(word: str) -> str:
    """One normalisation for keywords and words alike — the client's ``stem``, to the letter."""
    w = "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c)).lower()
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and re.search(r"(ss|x|ch|sh)es$", w):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


def terms(text: str) -> list[str]:
    words = [stem(w) for w in _WORD.findall(str(text or ""))]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


@lru_cache(maxsize=1)
def _index() -> dict[str, list[tuple[str, int]]]:
    ix: dict[str, list[tuple[str, int]]] = {}
    for g in _doc()["glyphs"]:
        for k in g["keywords"]:
            key = " ".join(stem(p) for p in k.lstrip("~").split())
            row = ix.setdefault(key, [])
            if not any(n == g["name"] for n, _ in row):
                row.append((g["name"], 1 if k.startswith("~") else 3))
    return ix


def match(name: str, purpose: str = "") -> Optional[str]:
    """The best keyword match for a workspace called ``name`` for ``purpose``, or ``None``."""
    ix = _index()
    score: dict[str, int] = {}
    for text, mult in ((name, 2), (purpose, 1)):
        for t in set(terms(text)):
            for n, w in ix.get(t, ()):
                score[n] = score.get(n, 0) + w * mult
    best, top = None, 0
    for g in _doc()["glyphs"]:
        s = score.get(g["name"], 0)
        if s > top:
            best, top = g["name"], s
    return best


def _fnv(s: str) -> int:
    """FNV-1a over UTF-16 code units — the client's ``identity.hashId``, to the bit."""
    h = 0x811C9DC5
    raw = s.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        h ^= raw[i] | (raw[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def generic(ws_id: str) -> str:
    """A neutral icon for a name nothing matched, stable for the workspace id."""
    pool = _doc()["generic"]
    return pool[_fnv(str(ws_id or "")) % len(pool)]


MODEL_SYSTEM = (
    "You pick an icon for a workspace from a fixed list of icon names. Reply with ONE JSON object and "
    "nothing else: {\"icon\": \"<one name from the list>\"}. Choose the icon a person would expect for "
    "what the workspace is about. Never invent a name."
)


def by_model(complete: Optional[Callable[[str, str], Optional[dict]]], name: str, purpose: str) -> Optional[str]:
    """Ask the small model (``complete(system, user)`` → one JSON object) for a listed name."""
    if complete is None:
        return None
    user = (f"Workspace name: {name[:120]}\nWhat it is for: {purpose[:400] or '(not given)'}\n"
            f"Icons: {', '.join(g['name'] for g in _doc()['glyphs'])}")
    try:
        out = complete(MODEL_SYSTEM, user) or {}
    except Exception:  # noqa: BLE001 — a model failure never stops a workspace from being made
        return None
    got = str(out.get("icon") or "").strip().lower()
    return got if got in _by_name() else None


def choose(name: str, purpose: str = "", ws_id: str = "", *,
           complete: Optional[Callable[[str, str], Optional[dict]]] = None) -> tuple[str, str]:
    """``(icon, by)``: the keyword match (``auto``), else the model's pick (``model``), else a neutral
    icon (``auto``)."""
    hit = match(name, purpose)
    if hit:
        return hit, "auto"
    got = by_model(complete, name, purpose)
    if got:
        return got, "model"
    return generic(ws_id or name), "auto"


# ── on the workspace ─────────────────────────────────────────────────────────────────────────────

def stored(front: dict) -> tuple[Optional[str], Optional[str]]:
    """``(glyph, glyph_by)`` from README front matter; an unknown name is ignored."""
    name = str(front.get("glyph") or "").strip().lower()
    by = str(front.get("glyph_by") or "").strip().lower()
    if not name or not _NAME.match(name) or not known(name):
        return None, None
    return name, (by if by in BY else "auto")


_HEAD = re.compile(r"^#\s+(.+?)\s*$", re.M)


def name_and_purpose(readme: str, front: dict, slug: str) -> tuple[str, str]:
    """What a README says the workspace is called and is for: front matter ``title``/``name`` or the
    first heading (else the slug's words), and the first plain paragraph after it."""
    body = readme
    if body.startswith("---"):
        end = body.find("\n---", 3)
        body = body[end + 4:] if end != -1 else body
    m = _HEAD.search(body)
    name = str(front.get("title") or front.get("name") or (m.group(1) if m else "")).strip()
    if not name:
        name = re.sub(r"-(?=[a-z]*\d)[a-z0-9]{3,8}$", "", slug or "").replace("-", " ")
    rest = body[m.end():] if m else body
    purpose = ""
    for para in re.split(r"\n\s*\n", rest):
        p = para.strip()
        if p and not p.startswith(("#", "{{", "---", "|", "<", "!", "-", "*", ">")):
            purpose = p
            break
    return name, purpose[:400]


def svg(name: str) -> bytes:
    """The icon as a standalone SVG — outline strokes, the theme's text colour where the viewer can
    say it — carrying ``data-vexa-glyph`` so the client draws it in the tile's own colours instead."""
    g = _by_name()[name]
    parts = []
    for tag, attrs in g["nodes"]:
        if tag not in ("path", "circle", "rect", "line", "polyline", "polygon", "ellipse"):
            continue
        a = " ".join(f"{k}={quoteattr(str(v))}" for k, v in attrs.items() if re.match(r"^[a-zA-Z-]+$", k))
        parts.append(f"<{tag} {a}/>")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" fill="none" '
            'stroke="#6b6f7a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
            f'data-vexa-glyph="{name}">'
            '<style>@media (prefers-color-scheme: dark){svg{stroke:#c3c6cf}}</style>'
            + "".join(parts) + "</svg>").encode("utf-8")
