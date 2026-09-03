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

    Entity ids are slugs (`olga-avramenko`), which carry neither. The test is on the SHAPE and not
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
    from pathlib import Path

    from workspaces.shared.entities import ENTITIES_DIR, KINDS

    base = Path(root) / ENTITIES_DIR
    out: dict = {}
    for kind in (kinds or KINDS):
        d = base / kind
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            if f.name != "index.md":
                out.setdefault(f.stem, workspace_id)
    return out
