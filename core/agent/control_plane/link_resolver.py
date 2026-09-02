"""link_resolver.py — what a link points at RIGHT NOW, answered per reader.

PRD decision 26.3: *"Resolution at read time. The panel asks the server what a link points to now:
readable → chip that opens; not yours → greyed chip with the title and 'in a workspace you don't
have' (by design, no error); gone → last known title. Nothing 404s inside a page."*

The whole reason resolution is a SERVER call and not a client lookup is the middle state. A client
can tell "I found it" from "I didn't"; only the server can tell "it exists and is not yours" from
"it is gone". Collapsing those two is how a product tells a person their colleague's page was
deleted when in fact they were simply never invited.

Pure over a workspace root plus the registry — no HTTP, no redis of its own — so the three access
states are provable offline, which is the only way to prove the middle one at all.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from control_plane import workspace_ids as ids_mod
from shared.entities import ENTITIES_DIR, KINDS, split_frontmatter
from shared.links import Ref, canonical_url, parse_ref

# Titles a resolver may show for a page it is not allowed to open. Deriving one from the ref is the
# ONLY honest option: the id `olga-avramenko` becomes "Olga Avramenko", which is what the writer
# typed before the rewrite. Reading the real title would mean reading a workspace this reader has
# no claim on, which is the thing the access state exists to prevent.
_WORD = re.compile(r"[-_]+")


def humanize(target: str) -> str:
    stem = str(target or "").rstrip("/").split("/")[-1]
    stem = re.sub(r"\.[a-z0-9]{1,5}$", "", stem, flags=re.I)
    return " ".join(w[:1].upper() + w[1:] for w in _WORD.split(stem) if w) or stem


def _title_of(path: Path, fallback: str) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return fallback
    fm, _ = split_frontmatter(text)
    for ln in fm:
        k, sep, v = ln.partition(":")
        if sep and k.strip() == "title":
            t = v.split("#")[0].strip()
            if t:
                return t
    return fallback


def escapes(target: str) -> bool:
    """Does this path ref walk OUT of the workspace it names?

    A link is text somebody (or some model) wrote into a document, so it is untrusted input on the
    read path exactly like a request parameter. ``..`` is refused at BOTH ends: the lookup below
    will not find a file through it, and ``resolve`` will not echo it back inside a URL — a URL the
    client would then hand to the file endpoint, where the same string gets a second chance."""
    parts = [p for p in str(target or "").split("/") if p and p != "."]
    depth = 0
    for p in parts:
        depth += -1 if p == ".." else 1
        if depth < 0:
            return True
    return False


def _find(root: Path, ref: Ref) -> Optional[str]:
    """The workspace-relative path this ref names, or ``None``.

    Three lookups, in the order of how stable the thing being matched is: a frontmatter ``id``
    (survives a rename), a path (survives nothing but a rename of the workspace), a title (the
    in-workspace form, matched by the same slug rule ``entity_upsert`` writes filenames with)."""
    if ref.form == "path":
        if escapes(ref.target):
            return None                            # traversal guard: a link can never escape
        return ref.target if (root / ref.target).is_file() else None
    from shared.entities import slugify

    slug = slugify(ref.target) if ref.form == "title" else ref.target
    for kind in KINDS:
        f = root / ENTITIES_DIR / kind / f"{slug}.md"
        if f.is_file():
            return f"{ENTITIES_DIR}/{kind}/{slug}.md"
    return None


def resolve(ref_text: str, *, subject: str, root, registry: ids_mod.WorkspaceRegistry,
            here: Optional[dict] = None, is_member=None) -> dict:
    """One ref → ``{ref, title, url, access, workspace}``.

    ``here`` is the reader's CURRENT workspace record (from the registry), used for the
    in-workspace ``[[Title]]`` form. Passing ``None`` for it is legitimate — a reader whose own
    workspace has no id yet — and the in-workspace form then resolves to a relative path with no
    canonical URL, which is exactly what it was before ids existed."""
    ref = parse_ref(ref_text)
    if ref.workspace is None:
        rec, access = here, (ids_mod.ACCESS_READABLE if here else ids_mod.ACCESS_GONE)
    else:
        rec = registry.get(ref.workspace)
        access = ids_mod.access_for(rec, subject, root=root, is_member=is_member)

    out = {"ref": ref_text, "title": humanize(ref.target), "url": None, "access": access,
           "workspace": (rec or {}).get("name"),
           # READABLE and WRITABLE are two answers, not one (founder ruling 2026-09-02: a desk is
           # readable by any signed-in member of this instance and writable only by its owner). A
           # client that cannot tell them apart either hides an editor a person may use or offers
           # one that will 403.
           "writable": ids_mod.writable_for(rec, subject, root=root, is_member=is_member)}
    if access != ids_mod.ACCESS_READABLE or not rec:
        return out

    ws_root = Path(rec.get("dir") or (Path(root) / str(rec.get("slug") or "")))
    rel = _find(ws_root, ref) if ws_root.is_dir() else None
    if rel is None:
        # READABLE AND NOT THERE. Deliberately not a fourth state: the reader may open it, and the
        # panel's own empty state is a better answer than a chip that refuses to be clicked (the
        # rule docLinks already follows for a missing [[wikilink]]). The url is the honest one.
        tail = ref.target if (ref.form == "path" and not escapes(ref.target)) else ""
        out["url"] = canonical_url(rec["id"], tail)
        out["missing"] = True
        return out
    out["title"] = _title_of(ws_root / rel, humanize(ref.target))
    out["url"] = canonical_url(rec["id"], rel)
    out["path"] = rel
    out["slug"] = rec.get("slug")
    return out


def resolve_many(refs, *, subject: str, root, registry: ids_mod.WorkspaceRegistry,
                 here: Optional[dict] = None, is_member=None, limit: int = 200) -> list[dict]:
    """A page's worth of refs in one round trip — the shape the panel actually needs.

    Capped: a document is rendered one screen at a time and a request that could ask for ten
    thousand resolutions is a directory walk somebody else pays for."""
    seen: dict[str, dict] = {}
    for r in list(refs or [])[:limit]:
        key = str(r)
        if key not in seen:
            seen[key] = resolve(key, subject=subject, root=root, registry=registry,
                                here=here, is_member=is_member)
    return list(seen.values())
