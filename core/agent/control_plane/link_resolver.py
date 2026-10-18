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

A MEETING IS A KIND HERE TOO (founder, 2026-10-11: *"we need a special label for a meeting alongside
people companies etc"*). A ref that names a meeting's notes page (`kg/entities/meeting/<name>.md`)
is answered as the meeting behind it — its title and date, `kind: "meeting"` — by the injected
``meeting`` resolver (`meeting_link.resolve`), which reads the row through the one meeting access
decision. That resolver holds the access callable; this module stays pure.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from control_plane import workspace_ids as ids_mod
from workspaces.shared.entities import ENTITIES_DIR, KINDS, split_frontmatter
from workspaces.shared.links import Ref, canonical_url, parse_ref
from workspaces.shared.workspace_paths import PathRefused, is_inside, relative_parts
from workspaces.shared import workspace_paths as wpaths

# Titles a resolver may show for a page it is not allowed to open. Deriving one from the ref is the
# ONLY honest option: the id `nora-quill` becomes "Nora Quill", which is what the writer
# typed before the rewrite. Reading the real title would mean reading a workspace this reader has
# no claim on, which is the thing the access state exists to prevent.
_WORD = re.compile(r"[-_]+")
#: A title lives in the front matter at the top of a page; this much of it is read.
_TITLE_HEAD_BYTES = 64 * 1024


def humanize(target: str) -> str:
    stem = str(target or "").rstrip("/").split("/")[-1]
    stem = re.sub(r"\.[a-z0-9]{1,5}$", "", stem, flags=re.I)
    return " ".join(w[:1].upper() + w[1:] for w in _WORD.split(stem) if w) or stem


def _title_of(root: Path, rel: str, fallback: str) -> str:
    """The page's ``title:`` — read NOFOLLOW (`workspace_paths`): the title is returned to the caller
    of `POST /api/links/resolve`, and the page is in a tree the model's tools can write."""
    text = wpaths.read_head_inside(root, rel, _TITLE_HEAD_BYTES)
    if text is None:
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
    read path exactly like a request parameter. It is refused at BOTH ends: the lookup below will
    not find a file through it, and ``resolve`` will not echo it back inside a URL — a URL the
    client would then hand to the file endpoint, where the same string gets a second chance.

    THE TEXTUAL half only, because this answers about a ref before any workspace root is known;
    ``_find`` re-asks ``workspaces.shared.workspace_paths`` with the root so a SYMLINK out is caught too. The
    version this replaces counted ``..`` segments and nothing else — and ``Path("/ws") / "/etc/passwd"``
    is ``/etc/passwd``, an absolute path DISCARDING the root, which is how ``[[ws:<id>//etc/passwd]]``
    read a host file and echoed its path back to the caller (R-A06)."""
    try:
        relative_parts(target)
    except PathRefused:
        return True
    return False


def _find(root: Path, ref: Ref) -> Optional[str]:
    """The workspace-relative path this ref names, or ``None``.

    Three lookups, in the order of how stable the thing being matched is: a frontmatter ``id``
    (survives a rename), a path (survives nothing but a rename of the workspace), a title (the
    in-workspace form, matched by the same slug rule ``entity_upsert`` writes filenames with)."""
    if ref.form == "path":
        # ONE guard, with the root, so absolute · `..` · symlink-out · `.git`/`.vexa` are all one
        # answer (shared/workspace_paths). A link can never escape the workspace it names.
        if not is_inside(root, ref.target):
            return None
        return ref.target if (root / ref.target).is_file() else None
    from workspaces.shared.entities import slugify

    slug = slugify(ref.target) if ref.form == "title" else ref.target
    for kind in KINDS:
        rel = f"{ENTITIES_DIR}/{kind}/{slug}.md"
        if wpaths.is_file_inside(root, rel):   # a page, not a link planted where one would be
            return rel
    return None


# ── across workspaces, by slug (#1783; founder: "cross workspace links do not work") ─────────────
#
# Two qualified spellings name the workspace by its SLUG, the name a person and the agent both see:
# `biz:graph/sg/Growth` and `vexa://ws/biz/graph/sg/Growth.md`. A bare slug (`[[oss-led-growth]]`)
# that is not a page here is looked for in every workspace the reader can READ. Matching is case-
# and dash-insensitive and `.md` is optional. A workspace the reader cannot read is never searched
# by a bare slug, and a qualified link into one answers `not-yours` with the ref's own words.
_QUAL_URL = re.compile(r"^vexa://ws/([A-Za-z0-9][\w.-]{0,63})/(.+)$")
_QUAL = re.compile(r"^([A-Za-z0-9][\w.-]{0,63}):(?!//)(.+)$")
_NOT_SLUGS = {"ws", "http", "https", "mailto", "vexa", "file", "data", "javascript"}
#: The most files one workspace is indexed through for a bare-slug lookup.
_INDEX_MAX = 20000


def _key(name: str) -> str:
    stem = re.sub(r"\.md$", "", str(name or ""), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def qualified(ref_text: str, registry) -> Optional[tuple[dict, str]]:
    """``(record, target)`` for a slug-qualified ref naming a registered workspace, else ``None``
    (so `Note: call back` stays the title it reads as)."""
    t = str(ref_text or "").strip()
    m = _QUAL_URL.match(t) or _QUAL.match(t)
    if not m or m.group(1).lower() in _NOT_SLUGS:
        return None
    rec = registry.by_slug(m.group(1))
    if rec is None:
        low = m.group(1).lower()
        rec = next((r for r in registry.all() if str(r.get("slug") or "").lower() == low), None)
    target = m.group(2).strip().lstrip("/")
    return (rec, target) if rec and target else None


def _md_index(root: Path, cache: Optional[dict] = None) -> dict:
    """``{key: [rel, ...]}`` of the workspace's pages, dot-directories skipped; bounded."""
    ck = str(root)
    if cache is not None and ck in cache:
        return cache[ck]
    out: dict[str, list[str]] = {}
    skip = lambda rel: rel.split("/")[-1].startswith(".")  # noqa: E731
    for n, rel in enumerate(wpaths.walk_files_inside(root, skip_dir=skip)):
        if n >= _INDEX_MAX:
            break
        name = rel.split("/")[-1]
        if name.lower().endswith(".md") and not name.startswith("."):
            out.setdefault(_key(name), []).append(rel)
    if cache is not None:
        cache[ck] = out
    return out


def _find_loose(root: Path, target: str, cache: Optional[dict] = None) -> Optional[str]:
    """A page by a path or a slug, case- and dash-insensitive, `.md` optional — inside ``root`` only."""
    if escapes(target):
        return None
    t = target.strip().strip("/")
    if "/" in t or re.search(r"\.md$", t, re.I):
        want = [_key(seg) for seg in t.split("/") if seg]
        rel_dir = ""
        for i, seg in enumerate(want):
            last = i == len(want) - 1
            names = (wpaths.list_files_inside(root, rel_dir) if last else wpaths.list_dirs_inside(root, rel_dir))
            hit = next((n for n in names if not n.startswith(".") and _key(n) == seg), None)
            if hit is None:
                return None
            rel_dir = f"{rel_dir}/{hit}" if rel_dir else hit
        return rel_dir if rel_dir.lower().endswith(".md") and wpaths.is_file_inside(root, rel_dir) else None
    hits = _md_index(root, cache).get(_key(t), [])
    return hits[0] if len(hits) == 1 and wpaths.is_file_inside(root, hits[0]) else None


def _is_own_desk(rec: dict, subject: str) -> bool:
    return rec.get("kind") == "desk" and str(subject or "") in (str(rec.get("owner") or ""), str(rec.get("slug") or ""))


def _ws_label(rec: dict, subject: str = "") -> Optional[str]:
    """A workspace's name for a person to read — never an address: the reader's own desk is
    "Personal", someone else's is "<their name>'s desk"."""
    name = str(rec.get("name") or "").strip()
    if "@" not in name:
        return name or None
    if rec.get("kind") == "desk" and _is_own_desk(rec, subject):
        return "Personal"
    local = name.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    return f"{local[:1].upper() + local[1:]}'s desk" if local else None


def _found(rec: dict, ws_root: Path, rel: str, out: dict, fallback: str, subject: str = "") -> dict:
    out["title"] = _title_of(ws_root, rel, fallback)
    out["url"] = canonical_url(rec["id"], rel)
    out["path"] = rel
    out["slug"] = rec.get("slug")
    out["workspace"] = _ws_label(rec, subject)
    return out


def _readable(subject: str, root, registry, is_member, within) -> list[dict]:
    recs = []
    for rec in registry.all():
        if ids_mod.access_for(rec, subject, root=root, is_member=is_member) != ids_mod.ACCESS_READABLE:
            continue
        if within is not None and not within(str(rec.get("slug") or "")):
            continue
        recs.append(rec)
    return sorted(recs, key=lambda r: str(r.get("slug") or ""))


def _ws_root(rec: dict, root) -> Path:
    return Path(rec.get("dir") or (Path(root) / str(rec.get("slug") or "")))


def _across(ref_text: str, target: str, *, subject: str, root, registry, here, is_member, within,
            cache: Optional[dict]) -> Optional[dict]:
    """A bare slug that is not a page here, looked for in every readable workspace."""
    hits = []
    for rec in _readable(subject, root, registry, is_member, within):
        if here and rec.get("id") == here.get("id"):
            continue
        wr = _ws_root(rec, root)
        if not wr.is_dir():
            continue
        for rel in _md_index(wr, cache).get(_key(target), []):
            if wpaths.is_file_inside(wr, rel):
                hits.append((rec, wr, rel))
    # ONE PAGE, NOT A PICKER (founder, live: a "Qonto 3" chip listed "Qonto · dmitry@vexa.ai" twice
    # and "Qonto · test@vexa.ai"): one file through two mounts is one hit; the reader's own desk and
    # the shared workspaces come first, and someone else's desk only when nothing else has the page.
    seen, own, shared, others = set(), [], [], []
    for rec, wr, rel in hits:
        try:
            real = str((wr / rel).resolve())
        except (OSError, ValueError):
            real = f"{rec.get('slug')}:{rel}"
        if real in seen:
            continue
        seen.add(real)
        if rec.get("kind") == "desk":
            (own if _is_own_desk(rec, subject) else others).append((rec, wr, rel))
        else:
            shared.append((rec, wr, rel))
    hits = (own + shared) or others
    if not hits:
        return None
    base = {"ref": ref_text, "title": humanize(target), "url": None, "access": ids_mod.ACCESS_READABLE}
    if len(hits) == 1:
        rec, wr, rel = hits[0]
        base["writable"] = ids_mod.writable_for(rec, subject, root=root, is_member=is_member)
        return _found(rec, wr, rel, base, humanize(target), subject)
    base["ambiguous"] = True
    base["writable"] = False
    base["workspace"] = None
    base["candidates"] = [_found(rec, wr, rel, {}, humanize(target), subject) for rec, wr, rel in hits[:12]]
    return base


def resolve(ref_text: str, *, subject: str, root, registry: ids_mod.WorkspaceRegistry,
            here: Optional[dict] = None, is_member=None, within=None, cache: Optional[dict] = None) -> dict:
    """One ref → ``{ref, title, url, access, workspace}``.

    ``here`` is the reader's CURRENT workspace record (from the registry), used for the
    in-workspace ``[[Title]]`` form. Passing ``None`` for it is legitimate — a reader whose own
    workspace has no id yet — and the in-workspace form then resolves to a relative path with no
    canonical URL, which is exactly what it was before ids existed.

    ``within`` (a slug -> bool) is a delegated dispatch's ceiling (`ceiling.reads_within`): a
    workspace outside it answers `not-yours` to this reader, whatever the account could read.
    ``None`` is a reader with no ceiling."""
    q = qualified(ref_text, registry) if not str(ref_text or "").strip().lower().startswith("ws:") else None
    if q is not None:
        rec, target = q
        access = ids_mod.access_for(rec, subject, root=root, is_member=is_member)
        readable = access == ids_mod.ACCESS_READABLE and (within is None or within(str(rec.get("slug") or "")))
        out = {"ref": ref_text, "title": humanize(target), "url": None,
               "access": access if (readable or access != ids_mod.ACCESS_READABLE) else ids_mod.ACCESS_NOT_YOURS,
               "workspace": rec.get("name") if readable else None,
               "writable": bool(readable and ids_mod.writable_for(rec, subject, root=root, is_member=is_member))}
        if not readable:
            return out
        wr = _ws_root(rec, root)
        rel = _find_loose(wr, target, cache) if wr.is_dir() else None
        if rel is None:
            out["missing"] = True
            out["url"] = canonical_url(rec["id"], "" if escapes(target) else target)
            return out
        return _found(rec, wr, rel, out, humanize(target))

    ref = parse_ref(ref_text)
    if ref.workspace is None:
        rec, access = here, (ids_mod.ACCESS_READABLE if here else ids_mod.ACCESS_GONE)
    else:
        rec = registry.get(ref.workspace)
        access = ids_mod.access_for(rec, subject, root=root, is_member=is_member)
        if rec and within is not None and not within(str(rec.get("slug") or "")):
            # Outside the ceiling: answered as a workspace this reader does not have, read nowhere.
            # The name follows the PERSON's own access: a member keeps it, a non-member gets none.
            return {"ref": ref_text, "title": humanize(ref.target), "url": None,
                    "access": ids_mod.ACCESS_NOT_YOURS if access == ids_mod.ACCESS_READABLE else access,
                    "workspace": rec.get("name") if access != ids_mod.ACCESS_NOT_YOURS else None,
                    "writable": False}

    out = {"ref": ref_text, "title": humanize(ref.target), "url": None, "access": access,
           # A workspace's name only for a reader with a claim on it: `not-yours` names nothing.
           "workspace": (rec or {}).get("name") if access != ids_mod.ACCESS_NOT_YOURS else None,
           # READABLE and WRITABLE are two answers, not one (founder ruling 2026-09-02: a desk is
           # readable by any signed-in member of this instance and writable only by its owner). A
           # client that cannot tell them apart either hides an editor a person may use or offers
           # one that will 403.
           "writable": ids_mod.writable_for(rec, subject, root=root, is_member=is_member)}
    if access != ids_mod.ACCESS_READABLE or not rec:
        return out

    ws_root = Path(rec.get("dir") or (Path(root) / str(rec.get("slug") or "")))
    rel = _find(ws_root, ref) if ws_root.is_dir() else None
    if rel is None and ref.workspace is None and ws_root.is_dir():
        rel = _find_loose(ws_root, ref.target, cache)
    if rel is None and ref.workspace is None:
        far = _across(ref_text, ref.target, subject=subject, root=root, registry=registry, here=rec,
                      is_member=is_member, within=within, cache=cache)
        if far is not None:
            return far
    if rel is None:
        # READABLE AND NOT THERE. Deliberately not a fourth state: the reader may open it, and the
        # panel's own empty state is a better answer than a chip that refuses to be clicked (the
        # rule docLinks already follows for a missing [[wikilink]]). The url is the honest one.
        tail = ref.target if (ref.form == "path" and not escapes(ref.target)) else ""
        out["url"] = canonical_url(rec["id"], tail)
        out["missing"] = True
        return out
    out["title"] = _title_of(ws_root, rel, humanize(ref.target))
    out["url"] = canonical_url(rec["id"], rel)
    out["path"] = rel
    out["slug"] = rec.get("slug")
    return out


def resolve_many(refs, *, subject: str, root, registry: ids_mod.WorkspaceRegistry,
                 here: Optional[dict] = None, is_member=None, limit: int = 200,
                 within=None, meeting=None) -> list[dict]:
    """A page's worth of refs in one round trip — the shape the panel actually needs.

    Capped: a document is rendered one screen at a time and a request that could ask for ten
    thousand resolutions is a directory walk somebody else pays for.

    ``meeting`` (ref -> answer | None) answers the refs that name a meeting; ``None`` from it means
    "not a meeting link", and the ref resolves as a page like any other."""
    seen: dict[str, dict] = {}
    cache: dict = {}
    for r in list(refs or [])[:limit]:
        key = str(r)
        if key in seen:
            continue
        answer = meeting(key) if meeting is not None else None
        if answer is not None:
            seen[key] = answer
        else:
            seen[key] = resolve(key, subject=subject, root=root, registry=registry,
                                here=here, is_member=is_member, within=within, cache=cache)
    return list(seen.values())


# ── what links HERE ("Mentioned in", #1783) ───────────────────────────────────────────────────────
_LINKS = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]|\]\(([^)\s]+)\)")
#: Files read for one backlinks answer, and the most answers returned.
_BACKLINK_FILES = 4000
_BACKLINK_HEAD = 64 * 1024


#: Files that list or describe a workspace rather than mention a page on purpose (founder, live: a
#: "Mentioned in" wall of README ×40, CLAUDE, AGENTS, Index): never a backlink.
_SYSTEM_FILES = {"readme.md", "claude.md", "agents.md", "index.md", "policies.md", "onboarding.md", "front.md"}


def _is_system_file(rel: str) -> bool:
    parts = [p for p in str(rel).split("/") if p]
    if not parts:
        return True
    if parts[-1].lower() in _SYSTEM_FILES:
        return True
    return any(p.startswith(("_", ".")) for p in parts) or parts[0].lower() in ("desk", "system", "inbox")


#: WORKING FILES — drafts, logs, audits, chat exports and dated working notes (founder, live: "20260615
#: 112823 C576", "2026 05 25 Pipeline Decisions Log", "2026 08 04 T 279 Website Claim Audit" in the
#: wall). They mention everything in passing; they count only when they link with a PATH.
_WORKING_DIRS = {"drafts", "draft", "logs", "log", "chats", "audits", "audit", "archive", "scratch", "tmp"}
_DATED = re.compile(r"^\d{4}-?\d{2}-?\d{2}")
_WORKING_NAME = re.compile(r"(^|[-_ ])(log|audit|scratch|draft)s?([-_ .]|$)", re.I)


def _is_working_file(rel: str) -> bool:
    parts = [p for p in str(rel).split("/") if p]
    if not parts:
        return False
    name = parts[-1]
    return (any(p.lower() in _WORKING_DIRS for p in parts[:-1]) or bool(_DATED.match(name))
            or bool(_WORKING_NAME.search(re.sub(r"\.mdx?$", "", name, flags=re.I))))


def _links_to(text: str, *, here_rec: dict, here_rel: str, slug: str, path: str, want: str, subject: str,
              root, registry, is_member, within, cache: dict) -> Optional[bool]:
    """Whether ``text`` (the page ``here_rel`` in ``here_rec``) LINKS to ``slug``/``path`` — a link
    that RESOLVES to that page, never a name that happens to match: a `[[name]]` resolves the way
    the reader's click would (from the linking page's own workspace), a markdown link from the
    linking page's own folder. ``None`` for no link; ``True`` for one spelled with a path (explicit),
    ``False`` for one by bare name."""
    found: Optional[bool] = None
    for m in _LINKS.finditer(text):
        raw = (m.group(1) or m.group(2) or "").strip()
        ref = raw.split("?")[0].split("#")[0]
        tail = ref.split(":", 1)[-1].rstrip("/").rsplit("/", 1)[-1]
        if _key(tail) != want:          # cheap: only refs that could name this page are resolved
            continue
        if m.group(2) is not None:       # a markdown link: a path, from the linking page's folder
            if re.match(r"^[a-z][a-z0-9+.-]*://|^mailto:", ref, re.I):
                continue
            base = here_rel.rsplit("/", 1)[0] if "/" in here_rel else ""
            parts: list[str] = []
            for seg in ((ref.lstrip("/") if ref.startswith("/") else f"{base}/{ref}" if base else ref).split("/")):
                if seg in ("", "."):
                    continue
                if seg == "..":
                    if parts:
                        parts.pop()
                    continue
                parts.append(seg)
            if here_rec.get("slug") == slug and "/".join(parts) == path:
                return True
            continue
        r = resolve(raw.split("|")[0], subject=subject, root=root, registry=registry, here=here_rec,
                    is_member=is_member, within=within, cache=cache)
        if r.get("slug") == slug and r.get("path") == path:
            if "/" in ref or qualified(ref, registry) is not None:
                return True
            found = False
    return found


def backlinks(slug: str, path: str, *, subject: str, root, registry, is_member=None, within=None,
              limit: int = 50) -> list[dict]:
    """Pages in workspaces this reader can READ that LINK to ``slug``/``path`` — a `[[name]]`,
    `[[slug:path]]` or markdown link that resolves to this very page (founder, live: a wall of ~40
    chips, most of them pages that only shared a name). System files never count; working files
    (drafts, logs, audits, dated notes) count only with a link spelled as a path. Never reads a
    workspace the reader cannot read, so nothing they may not see is named."""
    want = _key(str(path or "").rsplit("/", 1)[-1])
    if not want:
        return []
    out: list[dict] = []
    seen_files = 0
    seen_real: set = set()
    cache: dict = {}
    # a workspace's own front page is not "mentioned in" its own pages
    front = str(path).rsplit("/", 1)[-1].lower() in ("readme.md", "index.md")
    for rec in _readable(subject, root, registry, is_member, within):
        wr = _ws_root(rec, root)
        if not wr.is_dir():
            continue
        for key, rels in _md_index(wr, cache).items():
            for rel in rels:
                if rec.get("slug") == slug and (rel == path or front):
                    continue
                if _is_system_file(rel):
                    continue
                try:
                    real = str((wr / rel).resolve())
                except (OSError, ValueError):
                    real = f"{rec.get('slug')}:{rel}"
                if real in seen_real:          # one file through two mounts is one mention
                    continue
                seen_files += 1
                if seen_files > _BACKLINK_FILES:
                    return out
                text = wpaths.read_head_inside(wr, rel, _BACKLINK_HEAD) or ""
                if want.replace("-", "") not in re.sub(r"[^a-z0-9]", "", text.lower()):
                    continue                   # no ref could name it: skip the parse
                how = _links_to(text, here_rec=rec, here_rel=rel, slug=slug, path=path, want=want, subject=subject,
                                root=root, registry=registry, is_member=is_member, within=within, cache=cache)
                if how is None or (how is False and _is_working_file(rel)):
                    continue
                seen_real.add(real)
                name = str(rec.get("name") or "")
                out.append({"slug": rec.get("slug"), "path": rel, "workspace": None if "@" in name else (name or None),
                            "title": _title_of(wr, rel, humanize(rel))})
                if len(out) >= limit:
                    return out
    return out
