"""links.py — the link grammar, in one place, for every writer and every reader.

PRD decision 26.2. Three forms and one URL, and the whole point of writing them down here is that
the agent that WRITES a link, the endpoint that RESOLVES it and the client that RENDERS it must
agree about what a link is. Three spellings of one grammar is how a link renders in the panel and
dies in an email.

    [[Title]]                    in-workspace — unchanged, and still the common case
    [[ws:<workspace-id>/<id>]]   cross-workspace, by the target page's frontmatter `id`
    [[ws:<workspace-id>/<path>]] cross-workspace, by path — for files with no entity id
    /w/<workspace-id>/<path>     the ONE canonical URL: the same link in mail, chat and a doc

THE ID FORM IS THE STABLE ONE and the path form is the one that can break — that ordering is
deliberate and it is why the agent is told to prefer ids. A page's frontmatter `id` survives a
rename of its title; a path does not survive a move. Neither survives the workspace being renamed,
which is precisely why the WORKSPACE half is an id in both.

**A FILE REFERENCE ALWAYS NAMES ITS WORKSPACE** (founder ruling 2026-10-18: *"we must require full
paths to specific workspaces, we do not want to guess which readme this should open"*). A chat reply
listed `README.md` beside four files it had written into the Narrative workspace; the chip opened
the organisation's README, because a bare path was looked for in every workspace in turn and the
company layer came first. Every workspace has a `README.md`, so a bare path is a guess by
construction. The file reference is therefore

    [[page:<workspace>/<path>]]  — `<workspace>` a slug or a workspace id (`page_ref` writes it)

and a bare path (`README.md`, `kg/x.md`) is NOT a reference: no resolver searches for it, and the
client shows it as plain, unlinked text. The one context a bare path may be read in is the context
it was WRITTEN in: a relative link inside a page (that page's workspace), or an old chat message
whose session wrote into exactly one workspace (`qualify_bare_paths`, applied by the history read).

**A link to a workspace you do not have is not an error.** Founder: *"If a workspace is not
available, it's okay — by design."* So this module never raises on an unresolvable ref; it returns
what it could parse and lets the resolver say `not-yours` or `gone`. Nothing 404s inside a page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from workspaces.shared.workspace_id import is_workspace_id

# The wikilink itself — the same expression `shared/entities.py` uses, deliberately: two spellings
# of "what a wikilink is" is how a rewrite pass and a link counter disagree about the same file.
WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")

# The cross-workspace prefix. Lowercase `ws:` only — a case-insensitive prefix would make
# `[[WS:...]]` and `[[ws:...]]` two spellings of one link, and the agent writes these from a rule.
WS_PREFIX = "ws:"

# The canonical URL. One path shape, anchored, so a router and a mail template cannot drift.
CANONICAL_URL = re.compile(r"^/w/([a-z2-7]{10})(?:/(.*))?$")


@dataclass(frozen=True)
class Ref:
    """One parsed link reference.

    ``workspace`` is ``None`` for the in-workspace form — the reader's own workspace, resolved the
    way it always was. ``form`` says how ``target`` should be looked up: ``title`` searches entity
    pages by title, ``entity`` matches a page's frontmatter ``id``, ``path`` is a workspace-relative
    file path."""

    raw: str                      # the text between the brackets, verbatim — what a resolver echoes back
    workspace: Optional[str]      # a workspace id, or None for in-workspace
    target: str
    form: str                     # "title" | "entity" | "path"


def _form_of(target: str) -> str:
    """Entity id or path? A path is the one with structure in it — a slash, or a file extension.

    Entity ids are slugs (`nora-quill`), which carry neither. The test is on the SHAPE and not
    on a lookup, because the writer of the link and the reader of it are in different processes and
    the writer's directory listing is not available to the reader."""
    t = str(target or "")
    return "path" if ("/" in t or re.search(r"\.[a-z0-9]{1,5}$", t, re.I)) else "entity"


def parse_ref(inner: str) -> Ref:
    """Parse the text between ``[[ ]]``. TOTAL — anything that is not a well-formed ``ws:`` ref is
    an in-workspace title, which is what it looks like to a human too."""
    raw = str(inner or "").strip()
    if raw.lower().startswith(WS_PREFIX):
        rest = raw[len(WS_PREFIX):].lstrip("/")
        wid, _, target = rest.partition("/")
        wid = wid.strip()
        target = target.strip()
        if is_workspace_id(wid) and target:
            return Ref(raw=raw, workspace=wid, target=target, form=_form_of(target))
        # A malformed ws: ref is NOT silently downgraded to a title search: `[[ws:oops/x]]` looking
        # up an entity called "ws:oops/x" would render a "not found" chip and hide the typo. It is
        # returned as a path ref against an unknown workspace, which resolves to `gone` and SAYS so.
        return Ref(raw=raw, workspace=None, target=raw, form="path")
    return Ref(raw=raw, workspace=None, target=raw, form="title")


def format_ref(workspace_id: str, target: str) -> str:
    """The cross-workspace link, as written into a document.

    Bare — no ``|Display Name`` alias. The name is resolved at read time (decision 26.3: "the panel
    asks the server what a link points to NOW"), and a copy of the name inside the link would be a
    second store of it that goes stale on the first rename — the exact failure the id exists to
    end."""
    return f"[[{WS_PREFIX}{workspace_id}/{target}]]"


def canonical_url(workspace_id: str, path: str = "") -> str:
    """``/w/<workspace-id>/<path>`` — the one URL for a file, valid in mail, chat and a doc, and
    still valid after the workspace is renamed."""
    p = str(path or "").lstrip("/")
    return f"/w/{workspace_id}/{p}" if p else f"/w/{workspace_id}"


def parse_canonical_url(url: str) -> Optional[tuple[str, str]]:
    """``(workspace_id, path)`` for a canonical URL, else ``None``. Query and fragment are dropped —
    a mail client that appends tracking to a link must not make it unreadable."""
    u = str(url or "").split("#", 1)[0].split("?", 1)[0]
    m = CANONICAL_URL.match(u)
    if not m:
        return None
    return m.group(1), (m.group(2) or "")


def refs_in(text: str) -> list[Ref]:
    """Every wikilink in a piece of text, parsed, in order of appearance."""
    return [parse_ref(m.group(1)) for m in WIKILINK.finditer(str(text or ""))]


def cross_workspace_refs(text: str) -> list[Ref]:
    """Only the ``ws:`` ones — what a reader hands ``POST /api/links/resolve``."""
    return [r for r in refs_in(text) if r.workspace]


# ── the rewrite the agent's write-back leans on ──────────────────────────────────────────────────

def rewrite_cross_workspace(text: str, *, here: Iterable[str], elsewhere: dict,
                            slugify=None) -> tuple[str, list[tuple[str, str]]]:
    """Rewrite ``[[Title]]`` links whose page lives in ANOTHER mounted workspace into the id form.

    ``here`` is the entity slugs THIS workspace holds; ``elsewhere`` maps an entity slug to the id
    of the workspace that holds it. Returns ``(text, rewrites)`` where each rewrite is
    ``(original title, new ref)``.

    THE HOME WORKSPACE ALWAYS WINS. A name that has a page here is left as ``[[Title]]`` even when
    another mounted workspace also holds one — the reader's own desk is the page they meant, and a
    rewrite that quietly re-points a local link at somebody else's copy is worse than no rewrite.
    A name in neither is left alone too: it is a page nobody has written yet, and the write-back
    phase creates it HERE (decision 24), which the next pass then leaves local.

    Already-``ws:`` links are untouched — the pass is idempotent, which matters because it runs on
    every entity write and a page accumulates its links over many turns."""
    from workspaces.shared.entities import slugify as _slugify  # deferred: entities imports nothing from here

    slugify = slugify or _slugify
    here_set = {s for s in here if s}
    rewrites: list[tuple[str, str]] = []

    def _sub(m: re.Match) -> str:
        inner, alias = m.group(1), m.group(2)
        ref = parse_ref(inner)
        if ref.workspace or ref.form != "title":
            return m.group(0)                    # already an id link, or a path — leave it
        slug = slugify(ref.target)
        if not slug or slug in here_set:
            return m.group(0)                    # ours (or unslugifiable) — the home workspace wins
        wid = elsewhere.get(slug)
        if not wid:
            return m.group(0)                    # nobody's yet — decision 24 gives it a page HERE
        out = format_ref(wid, slug)
        rewrites.append((ref.target, out))
        return out if alias is None else out[:-2] + f"|{alias}]]"

    return WIKILINK.sub(_sub, str(text or "")), rewrites


def entity_slug_index(root, *, workspace_id: str, kinds=None, slugify=None) -> dict:
    """``{entity slug: workspace id}`` for one workspace's ``kg/entities/`` tree.

    Built by ``entity_upsert`` for each of the OTHER mounted workspaces, so a rewrite is a dict
    lookup rather than a directory walk per link."""
    from workspaces.shared.entities import ENTITIES_DIR, KINDS

    from workspaces.shared import workspace_paths as wpaths

    out: dict = {}
    for kind in (kinds or KINDS):
        # by descriptor, following no link: a linked kind folder or page is not this workspace's
        for name in wpaths.list_files_inside(root, f"{ENTITIES_DIR}/{kind}", suffix=".md"):
            if name != "index.md":
                out.setdefault(name[:-3], workspace_id)
    return out


# ── universal references (#1783) ─────────────────────────────────────────────────────────────────
#
# Founder, 2026-10-14: "references to workspaces, chats, documents, etc., accepted everywhere where
# access is confirmed." One format for every object, in two spellings that carry the same pair:
#
#     https://<host>/r/<kind>/<id>     pasteable anywhere — chat, a calendar body, an email, other apps
#     [[<kind>:<id>|<label>]]          the chip the agent writes; the label is only a hint for a
#                                      reader with no access to the server, never echoed back by it
#
# The id is the object's STABLE id: a workspace id, a chat id, `<workspace-id>/<path>` for a page,
# `<workspace-id>/<entity-id>` for an entity, a meeting's row id, a flow's or sensor's name. What a
# ref points at, and whether this reader may see it, is answered by the one resolver in agent-api
# (`control_plane/universal_refs.py`) — never by the text of the ref.
#
# A JOIN LINK (`/join/<token>`, `control_plane.join_links`) is a reference too, of kind `join`: one
# that carries a grant. It is recognised here so a calendar body yields it with the others.

#: One form for every object (PRD 0.13.4 D18, UI 7gg). `control_plane/page_grammar.py` uses this list.
REF_KINDS = ("workspace", "room", "chat", "page", "file", "entity", "meeting", "card", "person",
             "routine", "sensor", "message", "query", "series", "flow", "commit", "policy")
#: Older spellings that name the same kind (the rich-README `call:`; `flow:` stays its own kind here
#: because the resolver answers flows and routines through the same seam).
REF_KIND_ALIASES = {"call": "meeting"}
REF_PATH = "/r/"
_KIND_ALT = "|".join(REF_KINDS + tuple(REF_KIND_ALIASES))
_REF_ID = r"[^\s<>\"'#|\]\[]{1,300}"
_REF_URL_ID = r"[^\s<>\"'#?|\]\[]{1,300}"
REF_URL = re.compile(r"(?:https?://[^\s/<>\"']+)?/r/(" + _KIND_ALT + r")/(" + _REF_URL_ID + r")(?:#([\w=:.-]{1,120}))?")
_REF_CHIP = re.compile(r"^(" + _KIND_ALT + r"):(" + _REF_ID + r")(?:#([\w=:.-]{1,120}))?$")
_CHIP_TIME = re.compile(r"^(.+?)@((?:\d{1,2}:)?\d{1,2}:\d{2})$")
_JOIN_URL = re.compile(r"https?://[^\s/<>\"']+/join(?:/([A-Za-z0-9_-]{40,64})|\?i=([A-Za-z0-9_-]{40,64}))")
_TRAILING = ".,;:!)"


@dataclass(frozen=True)
class UniversalRef:
    kind: str                  # one of REF_KINDS, or "join"
    id: str
    label: Optional[str] = None
    #: A place inside the object (D18): ``t=00:12:30`` (a meeting moment), ``L42`` (a transcript
    #: line), a heading slug (a page). Never part of the access decision: the object decides.
    anchor: Optional[str] = None


def _anchor(kind: str, rid: str, frag: Optional[str]) -> tuple[str, Optional[str]]:
    """Split ``153@00:12:30`` into the id and ``t=00:12:30``; keep a ``#fragment`` as given."""
    a = (frag or "").strip() or None
    if kind == "meeting":
        m = _CHIP_TIME.match(rid)
        if m:
            rid, a = m.group(1), "t=" + m.group(2)
    if a and a.startswith("t="):
        parts = [int(x) for x in a[2:].split(":") if x.isdigit()]
        if len(parts) in (2, 3) and parts[-1] < 60:
            h, mi, se = ([0] + parts)[-3:]
            total = h * 3600 + mi * 60 + se
            a = f"t={total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"
        else:
            a = None
    return rid, a


def _clean_id(raw: str) -> Optional[str]:
    i = str(raw or "").strip().rstrip(_TRAILING).strip("/")
    if not i or any(seg in ("", ".", "..") for seg in i.split("/")):
        return None
    return i


def ref_url(kind: str, ref_id: str, *, base: str = "", anchor: Optional[str] = None) -> str:
    """``<base>/r/<kind>/<id>[#anchor]`` — the one URL for any object."""
    from urllib.parse import quote

    url = f"{str(base or '').rstrip('/')}{REF_PATH}{kind}/{quote(str(ref_id), safe='/-_.~@:')}"
    return url + (f"#{anchor}" if anchor else "")


def ref_chip(kind: str, ref_id: str, label: Optional[str] = None, anchor: Optional[str] = None) -> str:
    """``[[kind:id|label]]`` — the chip the agent writes (a meeting moment as ``id@hh:mm:ss``)."""
    rid = str(ref_id)
    if anchor:
        rid += f"@{anchor[2:]}" if kind == "meeting" and anchor.startswith("t=") else f"#{anchor}"
    return f"[[{kind}:{rid}|{label}]]" if label else f"[[{kind}:{rid}]]"


def parse_universal(text: str) -> Optional[UniversalRef]:
    """A URL, a ``[[kind:id|label]]`` chip, the chip's inner text, or a join link → the reference;
    anything else → ``None`` (a ``[[Title]]`` chip is not a typed reference)."""
    from urllib.parse import unquote

    t = str(text or "").strip()
    m = WIKILINK.fullmatch(t)
    inner, label = (m.group(1).strip(), (m.group(2) or "").strip() or None) if m else (t, None)
    c = _REF_CHIP.match(inner)
    if c:
        kind = REF_KIND_ALIASES.get(c.group(1), c.group(1))
        rid, anchor = _anchor(kind, c.group(2), c.group(3))
        i = _clean_id(rid)
        return UniversalRef(kind, i, label, anchor) if i else None
    frag = t.split("#", 1)[1] if "#" in t else None
    u = REF_URL.fullmatch(t.split("#", 1)[0].split("?", 1)[0])
    if u:
        kind = REF_KIND_ALIASES.get(u.group(1), u.group(1))
        rid, anchor = _anchor(kind, unquote(u.group(2)), frag)
        i = _clean_id(rid)
        return UniversalRef(kind, i, None, anchor) if i else None
    j = _JOIN_URL.fullmatch(t)
    if j:
        return UniversalRef("join", j.group(1) or j.group(2))
    return None


def universal_refs_in(text: str) -> list[UniversalRef]:
    """Every reference in a piece of text — chips, ``/r/`` URLs and join links — in order, once each."""
    from urllib.parse import unquote

    s = str(text or "")
    hits: list[tuple[int, UniversalRef]] = []
    for m in WIKILINK.finditer(s):
        r = parse_universal(m.group(0))
        if r:
            hits.append((m.start(), r))
    for m in REF_URL.finditer(s):
        if s[max(0, m.start() - 2):m.start()] == "[[":
            continue
        kind = REF_KIND_ALIASES.get(m.group(1), m.group(1))
        rid, anchor = _anchor(kind, unquote(m.group(2)), m.group(3))
        i = _clean_id(rid)
        if i:
            hits.append((m.start(), UniversalRef(kind, i, None, anchor)))
    for m in _JOIN_URL.finditer(s):
        hits.append((m.start(), UniversalRef("join", m.group(1) or m.group(2))))
    out: list[UniversalRef] = []
    seen: set = set()
    for _, r in sorted(hits, key=lambda h: h[0]):
        if (r.kind, r.id) not in seen:
            seen.add((r.kind, r.id))
            out.append(r)
    return out


def refs_as_urls(text: str, *, base: str) -> str:
    """For mail and anything else that is not our client: every ``[[kind:id|label]]`` chip becomes
    ``label (URL)`` (just the URL without a label). The URL is resolved for whoever opens it."""
    def _sub(m: re.Match) -> str:
        r = parse_universal(m.group(0))
        if not r or r.kind == "join":
            return m.group(0)
        url = ref_url(r.kind, r.id, base=base, anchor=r.anchor)
        return f"{r.label} ({url})" if r.label else url

    return WIKILINK.sub(_sub, str(text or ""))


# ── THE FILE REFERENCE (founder ruling 2026-10-18) ────────────────────────────────────────────────
#
# `[[page:<workspace>/<path>]]` is the one way to point at a file. These helpers write it, recognise
# the bare spellings that are NOT one, and qualify a bare path only against a workspace the caller
# already knows the text was written in. Nothing here searches.

#: A bare, workspace-relative document path: no leading slash, no `..`, ending in a page extension.
_BARE_DOC = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[\w.-]+(?:/[\w.\- ]+)*\.(?:md|markdown|mdx)$")
#: Inline code and fences, so prose can be rewritten without touching a code block.
_CODE_SPLIT = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")
#: A markdown link with a relative page target: `[label](README.md)`.
_MD_LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)")


def page_ref(workspace: str, path: str, label: Optional[str] = None) -> str:
    """``[[page:<workspace>/<path>|label]]`` — the qualified file reference. ``workspace`` is a slug or
    an id; a leading ``/`` on ``path`` is dropped (it is workspace-relative)."""
    ws = str(workspace or "").strip().strip("/")
    p = str(path or "").strip().lstrip("/")
    if not ws or not p:
        raise ValueError("a file reference needs a workspace and a path")
    return ref_chip("page", f"{ws}/{p}", label=label)


def is_bare_path(text: str) -> bool:
    """A workspace-relative page path that names no workspace (`README.md`, `kg/x.md`)."""
    return bool(_BARE_DOC.match(str(text or "").strip()))


def qualify_bare_paths(text: str, workspace: str) -> str:
    """Every bare page path in ``text`` — in inline code, or as a markdown link's target — rewritten as
    a reference into ``workspace``. Fenced blocks are literal and stay untouched.

    The CALLER vouches for ``workspace``: it must be the one context the text was written in (an old
    message whose session wrote into exactly one workspace). This function never chooses one."""
    ws = str(workspace or "").strip()
    if not ws:
        return str(text or "")

    def _code(seg: str) -> str:
        inner = seg[1:-1].strip()
        if not is_bare_path(inner):
            return seg
        return page_ref(ws, inner, label=inner.rsplit("/", 1)[-1])

    def _prose(seg: str) -> str:
        def _sub(m: re.Match) -> str:
            target = m.group(2).split("#", 1)[0]
            return page_ref(ws, target, label=m.group(1)) if is_bare_path(target) else m.group(0)
        return _MD_LINK.sub(_sub, seg)

    parts = _CODE_SPLIT.split(str(text or ""))
    return "".join(
        (part if part.startswith("```") else _code(part)) if i % 2 else _prose(part)
        for i, part in enumerate(parts))
