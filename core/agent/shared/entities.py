"""entities.py — the ONE write that turns something learned into a page in the knowledge graph.

PRD decision 24 (founder, 2026-09-02): *"how to update the agent so that it updates entities
whenever there is a chance for that? so it does not hesitate creating pages"*. Hesitation was never
a prompt problem — it was a **cost** problem. Recording one fact about a person meant: guess whether
a page exists, read it, invent a shape, merge without clobbering, remember the wikilink rule, and
commit. Six decisions for one sentence, so the agent skipped it and wrote prose instead. This module
collapses all six into one call that is safe to make on a maybe.

Deliberately a PURE FUNCTION OVER A DIRECTORY: no HTTP, no docker, no git required, so it is
offline-provable and the same code serves the control-plane endpoint, the MCP tool that forwards to
it, and the tests. Committing is a separate, optional step (``commit_entity``) — the caller owns the
repo, and a caller mid-turn on a workspace another writer owns must be able to write without
committing at all.

⚠ FRONTMATTER VOCABULARY. The tool's ARGUMENTS are the founder's words — ``kind`` and ``name`` — but
the KEYS written to the page are the ones this workspace's readers already use: ``type``, ``id``,
``title`` (``behavior/workspaces/default/kg/templates/*.md``, the terminal's wikilink resolver, the
per-type ``index.md`` files). Writing ``kind:``/``name:`` instead would have produced pages that look
right and are invisible to every existing reader — the exact defect ``workspace_write``'s CONFIG_VOCAB
guard exists to refuse. ``aliases``/``created``/``sources`` are added on top; nothing is renamed.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import unicodedata
from pathlib import Path

# The five kinds decision 24 names. A kind outside this set is refused rather than guessed into a new
# directory: a directory nothing indexes is a page nobody will ever find again.
KINDS = ("person", "company", "meeting", "project", "decision")

ENTITIES_DIR = "kg/entities"
INDEX_PATH = "kg/INDEX.md"
MISSING_PATH = "kg/MISSING.md"

_FRONTMATTER = re.compile(r"^---\n([\s\S]*?)\n---\n?")
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
# `## 2026-09-02` was the whole page before decision 24.6; `### 2026-09-02` under `## Timeline`
# is where the same entry lives now. BOTH are read, because "when was this page last actually
# learned about" has to keep answering across a migration that happens one page at a time —
# and the desk README orders its cards on this answer.
_DATED_HEADING = re.compile(r"^#{2,3}\s+(\d{4}-\d{2}-\d{2})\s*$", re.M)
_BULLET = re.compile(r"^\s*[-*]\s+(.+?)\s*$", re.M)

# The index is mounted into EVERY dispatch, so it is a prompt-budget line item, not a report.
INDEX_MAX_ROWS = 400


class EntityRefused(ValueError):
    """A write this module will not perform, carrying the reason the CALLER has to act on.

    Refusals are the point of the interface, not its edge: "a page carries only what was said or
    read, with its source" (decision 24.5) is enforceable only if a sourceless fact cannot be
    written at all. A rule that is merely asked for is a rule the model obeys about half the time —
    the grounding gate measured exactly that."""


def slugify(name: str) -> str:
    """kebab-case ascii, the shape the templates promise (``id: <slug>`` matches the filename)."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80]


def entity_rel_path(kind: str, name: str) -> str:
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise EntityRefused(f"{kind!r} is not an entity kind — one of {', '.join(KINDS)}")
    slug = slugify(name)
    if not slug:
        raise EntityRefused("an entity needs a name that survives slugification")
    return f"{ENTITIES_DIR}/{kind}/{slug}.md"


def split_frontmatter(text: str) -> tuple[list[str], str]:
    """``(frontmatter lines, body)``. Lines are kept RAW — comments, ordering, keys this module has
    never heard of. A page a human edited must come back out the way they left it."""
    m = _FRONTMATTER.match(text or "")
    if not m:
        return [], text or ""
    return m.group(1).splitlines(), text[m.end():]


def _fm_get(lines: list[str], key: str) -> str | None:
    for ln in lines:
        k, sep, v = ln.partition(":")
        if sep and k.strip() == key:
            return v.split("#")[0].strip()
    return None


def _fm_set(lines: list[str], key: str, value: str) -> list[str]:
    out, done = [], False
    for ln in lines:
        k, sep, _ = ln.partition(":")
        if sep and k.strip() == key and not done:
            out.append(f"{key}: {value}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"{key}: {value}")
    return out


def _list_field(raw: str | None) -> list[str]:
    raw = (raw or "").strip()
    if not raw or raw in ("[]", "~", "null"):
        return []
    return [p.strip() for p in raw.strip("[]").split(",") if p.strip()]


def _render_list(items) -> str:
    seen, out = set(), []
    for i in items:
        i = str(i).strip()
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return "[" + ", ".join(out) + "]"


def _normalise(fact: str) -> str:
    """What "the same fact" means for idempotency: same words, ignoring case, punctuation and the
    wikilink brackets. Without dropping the brackets, a fact re-stated after its subject got a page
    would append a near-duplicate line, and the page grows by a paragraph per turn forever."""
    s = _WIKILINK.sub(r"\1", str(fact or ""))
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return " ".join(s.split())


# Every recorded fact ends `- <fact> - source: <where it came from>`, so the stored line and the
# fact the caller passes are never byte-identical. Comparing them raw made "idempotent on identical facts"
# quietly false: the same sentence, re-stated next turn, appended again with a second source. Strip
# the attribution before comparing — the fact is the claim, not the provenance of this telling.
_SOURCE_SUFFIX = re.compile(r"\s+—\s+source:\s.*$")


def existing_facts(text: str) -> set[str]:
    out = set()
    for b in _BULLET.findall(text or ""):
        n = _normalise(_SOURCE_SUFFIX.sub("", b))
        if n:
            out.add(n)
    return out


def wikilinks(texts) -> list[str]:
    out, seen = [], set()
    for t in texts:
        for name in _WIKILINK.findall(str(t or "")):
            n = name.strip()
            if n and n.lower() not in seen:
                seen.add(n.lower())
                out.append(n)
    return out


def resolve_links(root, names) -> tuple[list[str], list[str]]:
    """``(resolved, unresolved)`` — which ``[[Name]]`` already have a page anywhere under
    ``kg/entities/``. Unresolved names are RETURNED, never auto-created: a page minted from a name
    with no facts behind it is the invention decision 24.5 forbids, and the kg-links rule already
    says a wikilink with no page renders as an inert "not found" chip. The caller upserts them with
    their own source; the tool result says so in words."""
    base = Path(root) / ENTITIES_DIR
    resolved, unresolved = [], []
    for n in names:
        slug = slugify(n)
        hit = any((base / k / f"{slug}.md").exists() for k in KINDS) if slug else False
        (resolved if hit else unresolved).append(n)
    return resolved, unresolved


def _today(today: str | None) -> str:
    return today or _dt.date.today().isoformat()


# ── cross-workspace links (PRD decision 26.3) ────────────────────────────────────────────────────
#
# The agent mounts several workspaces at once and writes about a person who has a page in one of the
# others. Written as `[[Olga Avramenko]]` that link resolves by TITLE, in whichever mount the reader
# searches first, and it dies the moment either workspace is renamed. Written as
# `[[ws:<workspace-id>/<entity-id>]]` it is two ids, and ids do not move.
#
# The agent is TOLD this rule (worker/engine.kg_links_preamble) and it is also DONE here, on the way
# to disk. Both, deliberately: a rule the model follows most of the time plus a rewrite that is
# always right beats either alone, and the rewrite is the half that can be tested.


def _elsewhere_index(root, mounts) -> dict:
    """``{entity slug: workspace id}`` for every mounted workspace EXCEPT this one.

    Skips a mount with no identity file rather than raising: a workspace the migration has not
    reached is one that cannot yet be linked to by id, and a turn must still be able to write."""
    from shared.links import entity_slug_index
    from shared.workspace_id import workspace_id_of

    here = Path(root).resolve()
    out: dict = {}
    for m in mounts or []:
        if not isinstance(m, dict):
            continue
        raw = str(m.get("path") or "")
        if not raw:
            continue
        p = Path(raw).resolve()
        if p == here or not p.is_dir():
            continue
        wid = m.get("id") or workspace_id_of(p)
        if not wid:
            continue
        for slug, w in entity_slug_index(p, workspace_id=str(wid)).items():
            out.setdefault(slug, w)
    return out


def _rewrite_links(root, facts, *, name: str, mounts) -> tuple[list, list]:
    """``(facts, rewrites)`` — the facts with cross-workspace links in id form."""
    elsewhere = _elsewhere_index(root, mounts)
    if not elsewhere:
        return list(facts), []
    from shared.links import rewrite_cross_workspace

    here = known_slugs(root) | {slugify(name)}   # the page being written counts as HERE, before it exists
    out, all_rewrites = [], []
    for f in facts:
        text, rewrites = rewrite_cross_workspace(f, here=here, elsewhere=elsewhere)
        out.append(text)
        all_rewrites += rewrites
    return out, all_rewrites


# ── dated facts (PRD decision 31 §3) ─────────────────────────────────────────────────────────────
#
# A meeting page carries WHEN, in frontmatter, so that the desk README's `Now` section and the
# timeline read the same fact instead of two descriptions of it (`shared/desk_now.py` is the
# reader). Three keys, closed set, ISO-8601 UTC. A closed set on purpose: an open one turns
# frontmatter into a scratchpad, and the value of these is that a reader knows what it will find.
DATE_FIELDS = ("scheduled_at", "held_at", "report_delivered_at", "due_at")


def _as_iso(value) -> str:
    """ISO-8601 `Z` from an epoch or an ISO string; "" for anything else.

    Naive strings are UTC: every timestamp this system writes is UTC, and guessing local would move
    a meeting by hours on a host whose clock is not the deployment's, silently.
    """
    import datetime as _dt
    if value in (None, "", []):
        return ""
    if isinstance(value, (int, float)):
        dt = _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc)
    else:
        text = str(value).strip()
        try:
            dt = _dt.datetime.fromtimestamp(float(text), _dt.timezone.utc)
        except ValueError:
            try:
                dt = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return ""
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _dated(dates) -> dict:
    """The whitelisted, normalised subset of what a caller passed. Anything else is dropped."""
    if not isinstance(dates, dict):
        return {}
    out = {}
    for key in DATE_FIELDS:
        iso = _as_iso(dates.get(key))
        if iso:
            out[key] = iso
    return out


# ── the card (PRD decision 24.6) ─────────────────────────────────────────────────────────────────
#
# Founder, 2026-09-02, on a page this tool had just made — a title, a date, one bullet:
# *"where is this format coming from? this is flat — not what we want."*
#
# It was flat because the writer only ever knew how to APPEND. A page assembled by appending is a
# log with a heading, and a log answers "what happened on the 2nd", which is not the question anyone
# opens a person's page to ask. The templates in `kg/templates/` have always described the right
# thing — a summary, sections for what it is and how it relates, the web of links — and nothing
# rendered them, so the shape lived in a file the agent was told to copy by hand and therefore did
# not. This module now renders that shape, and the log moves to `## Timeline` where a log belongs.
#
# The templates stay the human-readable statement of the shape; these maps are the executable one,
# and `test_card_shape_matches_the_templates` is what keeps them from drifting apart.

CARD_SECTIONS = {
    "person":   ("Role and organisation", "What they care about", "How we relate"),
    "company":  ("What it is", "People", "Our relationship"),
    "meeting":  ("When and who", "Decided", "Committed"),
    "project":  ("What it is", "Who", "Status"),
    "decision": ("What was decided", "Why", "What it changes"),
}

# Every card ends the same way, whatever it is about: who it touches, where it came from, what we
# still do not know, and the log. The order is deliberate — the reader wants the web before the
# provenance, and the provenance before the history.
TAIL_SECTIONS = ("Connected", "Sources", "Open questions", "Timeline")

# THE FIELD → SECTION MAP. This is what lets the model file a fact in place instead of dropping it
# at the bottom: it passes `fields={"role": "Chairs the TSC"}` and the sentence lands under *Role
# and organisation*, on this kind of page, without the model knowing the section names at all. The
# tool description is generated from this map (`tool_sections_text`), so the two cannot disagree.
FIELD_SECTION = {
    "person":   {"role": "Role and organisation", "company": "Role and organisation",
                 "cares_about": "What they care about", "relationship": "How we relate"},
    "company":  {"what": "What it is", "people": "People", "relationship": "Our relationship"},
    "meeting":  {"when": "When and who", "who": "When and who", "participants": "When and who",
                 "decided": "Decided", "committed": "Committed"},
    "project":  {"what": "What it is", "who": "Who", "status": "Status"},
    "decision": {"what": "What was decided", "why": "Why", "changes": "What it changes"},
}

# A label is worth writing only when the section does not already say what the line is. Under
# *People* a bullet reading "Person: [[Jane]]" says "person" twice; under *Role and organisation*
# "Company: [[Acme]]" earns its label because the section holds two different kinds of line.
FIELD_LABEL = {"role": "Role", "company": "Company", "cares_about": "Cares about",
               "relationship": "Relationship"}

# WHICH FIELDS ARE ALSO EDGES, and what the edge says from each end. Naming a person's `company` is
# the commonest way a graph gets half-built: their page links out, the company's page never learns
# it has an employee, and the web only works in the direction somebody happened to be writing.
# `(from this page, from the other page)`.
FIELD_LINKS = {
    "person":   {"company": ("works at", "works here")},
    "company":  {"people": ("works here", "works at")},
    "meeting":  {"participants": ("attendee", "attended"), "who": ("attendee", "attended")},
    "project":  {"who": ("works on this", "works on")},
    "decision": {},
}

_RECIPROCAL = {"works at": "works here", "works here": "works at",
               "attendee": "attended", "attended": "attendee",
               "owns": "owned by", "owned by": "owns",
               "part of": "includes", "includes": "part of",
               "decided in": "decided", "decided": "decided in"}

_H2 = re.compile(r"^##\s+(.+?)\s*$")
_H3 = re.compile(r"^###\s+(.+?)\s*$")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sections_for(kind: str) -> tuple:
    return CARD_SECTIONS.get(kind, ())


def tool_sections_text() -> str:
    """The sections, per kind, as the tool description states them.

    Generated rather than written out, because a tool description that lists sections the renderer
    does not have is worse than one that lists none: the model files a fact into a heading that then
    silently becomes an 'extra' section nobody reads."""
    lines = []
    for kind in KINDS:
        fields = ", ".join(sorted(FIELD_SECTION.get(kind, {})))
        lines.append(f"- {kind}: " + " · ".join(CARD_SECTIONS[kind]) + f"  (fields: {fields})")
    return "\n".join(lines)


class Card:
    """A parsed entity page, kept as RAW LINES.

    ⚠ THE FIRST VERSION OF THIS PARSED A PAGE INTO A MODEL AND RE-RENDERED THE WHOLE THING, and on a
    page a human had written that lost things nobody agreed to lose (F98, found by round-tripping
    real pages): paragraphs collapsed into one line, blank lines between them vanished, and a `##`
    *inside a code fence* was promoted to a real heading — which silently cut the fence in half and
    made everything after it a section. A tool that rewrites what it did not write is a tool people
    stop letting near their notes, and the damage is invisible until someone opens the page.

    So the page is a HEAD plus a list of BLOCKS, each holding the exact source lines of one `##`
    section, and rendering is a join. Nothing outside the sections this tool owns is ever
    regenerated: `render(parse(x)) == x`, byte for byte, for any page — which is a property test
    (`test_render_of_parse_is_the_identity_on_arbitrary_markdown`), not a claim.

    Lines are split on `"\\n"` rather than with `splitlines()` on purpose: `splitlines()` cannot tell
    `"a\\nb"` from `"a\\nb\\n"`, and a renderer that adds or drops the final newline of every page it
    touches makes a diff out of nothing."""

    __slots__ = ("head", "blocks", "was_flat")

    def __init__(self):
        self.head: list = []          # raw lines before the first heading this tool can see
        self.blocks: list = []        # [[name, [raw lines, heading first]], …]
        self.was_flat = False

    # ── reading ──────────────────────────────────────────────────────────────────────────────────

    def index(self, name: str) -> int:
        want = (name or "").strip().lower()
        for i, (head, _lines) in enumerate(self.blocks):
            if head.strip().lower() == want:
                return i
        return -1

    def content(self, name: str) -> list:
        """The section's lines WITHOUT its heading — what a reader of that section sees."""
        i = self.index(name)
        return self.blocks[i][1][1:] if i >= 0 else []

    @property
    def title(self) -> str:
        for line in self.head:
            if line.startswith("# "):
                return line[2:].strip()
        return ""

    @title.setter
    def title(self, value: str) -> None:
        for i, line in enumerate(self.head):
            if line.startswith("# "):
                self.head[i] = f"# {value}"
                return
        self.head = ["", f"# {value}", ""] if self.head else ["", f"# {value}", ""]

    @property
    def summary(self) -> str:
        seen_title = False
        for line in self.head:
            if line.startswith("# ") and not seen_title:
                seen_title = True
                continue
            if seen_title and line.strip():
                return line.strip()
        return ""

    @summary.setter
    def summary(self, value: str) -> None:
        if self.summary or not value.strip():
            return
        for i, line in enumerate(self.head):
            if line.startswith("# "):
                self.head[i + 1:i + 1] = ["", value.strip()]
                return
        self.head += ["", value.strip()]

    # ── writing, only into sections this tool owns ───────────────────────────────────────────────

    def ensure(self, name: str, at: int = -1) -> int:
        i = self.index(name)
        if i >= 0:
            return i
        # heading · the blank under it · the blank that separates it from whatever comes next. A
        # block owns its own trailing blank, otherwise the first bullet appended to a section welds
        # the NEXT heading onto it — which is what the first version of this did.
        block = [name, [f"## {name}", "", ""]]
        at = len(self.blocks) if at < 0 or at > len(self.blocks) else at
        self.blocks.insert(at, block)
        return at

    def _has(self, lines, line: str) -> bool:
        want = _normalise(_SOURCE_SUFFIX.sub("", line.lstrip("-* ")))
        return any(_normalise(_SOURCE_SUFFIX.sub("", l.lstrip("-* "))) == want
                   for l in lines if l.strip())

    def place(self, name: str) -> int:
        """The index of an owned section, creating it in CANONICAL order if it is missing.

        `ensure` appends, which is right for a caller that has already worked out where the section
        goes and wrong for everyone else: `link_back` reached a page whose `## Connected` did not
        exist yet and appended it after `## Sources`. A tail section knows where it belongs."""
        i = self.index(name)
        if i >= 0 or name not in TAIL_SECTIONS:
            return self.ensure(name)
        n = TAIL_SECTIONS.index(name)
        at = len(self.blocks)
        for later in TAIL_SECTIONS[n + 1:]:
            j = self.index(later)
            if j >= 0:
                at = min(at, j)
        return self.ensure(name, at)

    def add(self, name: str, line: str) -> bool:
        """Append a bullet to one owned section unless the page already carries that fact."""
        line = line.strip()
        if not line:
            return False
        i = self.place(name)
        lines = self.blocks[i][1]
        if self._has(lines[1:], line):
            return False
        _append_in_block(lines, line if line.startswith("-") else f"- {line}")
        return True

    def set_section(self, name: str, content: list) -> bool:
        """Replace one owned section's content. Only ``## Sources`` uses this — it is derived from
        frontmatter, so it is the one section whose body is not something anybody typed."""
        i = self.place(name)
        want = [f"## {name}", ""] + list(content) + [""]
        if self.blocks[i][1] == want:
            return False
        self.blocks[i][1] = want
        return True

    def add_timeline(self, day: str, line: str) -> bool:
        line = line if line.startswith("-") else f"- {line}"
        i = self.ensure("Timeline")
        lines = self.blocks[i][1]
        if self._has(lines[1:], line):
            return False
        head = f"### {day}"
        for j, l in enumerate(lines):
            if l.strip() == head:
                end = j + 1
                while end < len(lines) and not lines[end].startswith("### "):
                    end += 1
                while end > j + 1 and not lines[end - 1].strip():
                    end -= 1
                lines.insert(end, line)
                return True
        _append_in_block(lines, head, "", line)
        return True


def _append_in_block(lines: list, *new: str) -> None:
    """Append inside a section, BEFORE its trailing blank lines — so the blank line that separates
    it from the next section stays where it is instead of migrating to the middle.

    The blank line immediately AFTER a heading is never consumed: on an empty section it is the only
    blank there is, and eating it renders `## What they care about` with the first bullet welded to
    it, which is not the shape every other section on the page has."""
    floor = 2 if len(lines) > 1 and not lines[1].strip() else 1
    end = len(lines)
    while end > floor and not lines[end - 1].strip():
        end -= 1
    lines[end:end] = list(new)


# ── fences and comments are OPAQUE ───────────────────────────────────────────────────────────────
#
# F98's third mangle: a `## Setup` inside a ```markdown fence became a real heading, which cut the
# fence in half and turned the rest of the page into sections. Everything between the fence markers
# is text ABOUT markdown, never markdown, and the same goes for an HTML comment — the seed's own
# templates carry `<!-- the web: link every entity … -->` with prose inside it.
_FENCE_OPEN = re.compile(r"^\s{0,3}(```+|~~~+)")


def _opaque_mask(lines) -> list:
    """[bool] per line — True where a heading must NOT be recognised."""
    mask, fence, comment = [], "", False
    for line in lines:
        if fence:
            mask.append(True)
            if line.strip().startswith(fence):
                fence = ""
            continue
        if comment:
            mask.append(True)
            if "-->" in line:
                comment = False
            continue
        m = _FENCE_OPEN.match(line)
        if m:
            fence = m.group(1)[:3]
            mask.append(True)
            continue
        stripped = line.strip()
        if stripped.startswith("<!--"):
            mask.append(True)
            comment = "-->" not in stripped[4:]
            continue
        mask.append(False)
    return mask


def parse_card(body: str) -> Card:
    """Read any page — a card, a flat log, or something a human wrote — into head + blocks.

    Every line is kept exactly as it arrived. A top-level `## 2026-09-02` is the OLD FLAT SHAPE and
    is only NOTED here (`was_flat`); the move into `## Timeline` happens in `migrate_flat`, which
    the write path calls, so parsing stays a pure read and the round trip stays an identity."""
    card = Card()
    lines = (body or "").split("\n")
    mask = _opaque_mask(lines)
    current = None
    for line, opaque in zip(lines, mask):
        m = _H2.match(line) if not opaque else None
        if m:
            name = m.group(1).strip()
            if _ISO_DAY.match(name):
                card.was_flat = True
            current = [name, [line]]
            card.blocks.append(current)
            continue
        (current[1] if current is not None else card.head).append(line)
    if not card.blocks:
        card.was_flat = True                     # a bare page is as flat as a logged one
    return card


def render_card(card: Card, kind: str = "", fm: "list | None" = None) -> str:
    """head + every block, verbatim. ``kind`` is accepted and unused: the SHAPE is applied by
    ``ensure_card_shape`` on the write path, never by rendering, because a renderer that also
    reshapes cannot be an identity and this one has to be."""
    body = "\n".join(card.head + [l for _name, lines in card.blocks for l in lines])
    if fm is None:
        return body
    return "---\n" + "\n".join(fm) + "\n---\n" + body


def migrate_flat(card: Card) -> bool:
    """Move top-level `## <date>` sections into `## Timeline` as `### <date>`. The flat shape the
    founder rejected, converted on touch — for both reasons a page can be flat: this tool wrote it
    before today, or a person typed it."""
    dated = [(i, name, lines) for i, (name, lines) in enumerate(card.blocks) if _ISO_DAY.match(name)]
    if not dated:
        return False
    for i, _n, _l in reversed(dated):
        card.blocks.pop(i)
    t = card.ensure("Timeline")
    for _i, name, lines in dated:
        content = [l for l in lines[1:] if l.strip()]
        _append_in_block(card.blocks[t][1], f"### {name}", "", *content)
    return True


def ensure_card_shape(card: Card, kind: str) -> None:
    """Put the sections this tool owns where they belong, WITHOUT moving anything else.

    A missing kind-section lands after the last kind-section that precedes it; a missing tail
    section lands before the first tail section that follows it, else at the end. Sections a human
    wrote are never reordered — the card is imposed around their page, not over it."""
    named = list(CARD_SECTIONS.get(kind, ()))
    for n, head in enumerate(named):
        if card.index(head) >= 0:
            continue
        at = 0
        for earlier in named[:n]:
            j = card.index(earlier)
            if j >= 0:
                at = j + 1
        card.ensure(head, at)
    for head in TAIL_SECTIONS:
        card.place(head)


def drop_empty_tail(card: Card) -> None:
    """A `## Timeline` that has never had an entry helps nobody. Kind sections stay even when empty
    — the heading is how the next turn knows where the fact it just learned goes."""
    for head in TAIL_SECTIONS:
        i = card.index(head)
        if i >= 0 and not any(l.strip() for l in card.blocks[i][1][1:]):
            card.blocks.pop(i)


def find_entity(root, name: str) -> "tuple[str, str] | None":
    """`(kind, relative path)` of an existing page for this name, anywhere under `kg/entities/`."""
    slug = slugify(name)
    if not slug:
        return None
    base = Path(root) / ENTITIES_DIR
    for kind in KINDS:
        if (base / kind / f"{slug}.md").exists():
            return kind, f"{ENTITIES_DIR}/{kind}/{slug}.md"
    return None


def _chip(name: str, relation: str) -> str:
    rel = (relation or "").strip()
    return f"- [[{name}]] — {rel}" if rel else f"- [[{name}]]"


def link_back(root, target_name: str, from_name: str, relation: str) -> "str | None":
    """Put the reciprocal chip on the OTHER page, and return its path if anything changed.

    Only onto a page that already exists. Minting one from a name with no facts behind it is the
    invention decision 24.5 forbids — the caller gets it back in `links_missing` instead, and the
    edge completes the moment that page is written for a real reason.

    It adds ONE bullet to ONE section it owns. The rest of that page — someone else's page, which
    this call is only passing through — comes back out byte for byte."""
    hit = find_entity(root, target_name)
    if not hit:
        return None
    kind, rel = hit
    path = Path(root) / rel
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(text)
    card = parse_card(body)
    if not card.title:
        card.title = _fm_get(fm, "title") or target_name
    if not card.add("Connected", _chip(from_name, relation)):
        return None
    path.write_text(render_card(card, kind, fm), encoding="utf-8")
    return rel


def _as_lines(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def upsert_entity(root, kind: str, name: str, facts=(), source: str = "", *,
                  today: str | None = None, aliases=(), mounts=None, dates=None,
                  summary: str = "", fields=None, section: str = "",
                  connections=(), open_questions=()) -> dict:
    """Create or update ``kg/entities/<kind>/<slug>.md`` AS A CARD (PRD decision 24.6).

    The page is always parsed and re-rendered, never appended to, and that one decision carries the
    migration: a flat page — the shape the founder rejected, a title and a stack of dated bullets —
    parses into a card whose entries are all in `Timeline`, and rendering it produces the sections.
    Nothing has to remember to migrate anything.

    WHERE A FACT GOES:
      * ``fields`` — ``{"role": "Chairs the TSC"}`` — lands in the section this kind maps that field
        to (``FIELD_SECTION``), which is how the model files in place without knowing the headings.
        A field that is also an edge (``FIELD_LINKS``) additionally writes a ``## Connected`` chip
        HERE and the reciprocal chip on the other page.
      * ``facts`` — plain sentences — land in ``section=`` when it names one of this kind's
        sections, and in ``## Timeline`` under today when it does not. Timeline is the log; it is
        the fallback on purpose, so an unfiled fact is still kept and still dated.
      * ``connections`` — ``["Acme"]`` or ``[{"name": "Acme", "relation": "works at"}]`` — chips,
        both ways.
      * ``open_questions`` — the gaps, on the page, where the next turn can see them.
      * ``summary`` — the one line under the title. Set only when the page has none: the first
        sentence written about something is usually the person's own, and a tool that rewrites it
        every turn is a tool people stop letting near their pages.

    Idempotent on identical facts: a bullet already on the page (by ``_normalise``, ignoring its
    source clause) is dropped, and a call whose every fact is already there writes nothing and
    reports ``changed: False``. That is what makes the forced write-back phase safe on EVERY turn.

    ``mounts`` turns on PRD decision 26.3's write rule: a ``[[Name]]`` whose page lives in ANOTHER
    mounted workspace is rewritten to ``[[ws:<workspace-id>/<entity-id>]]`` before it is stored, so
    the link survives that workspace being renamed. The home workspace always wins.

    ``dates`` records WHEN in frontmatter for the keys in ``DATE_FIELDS`` — how the desk README's
    `Now` section and the timeline stay in agreement (decision 31 §3): both read these fields,
    neither parses prose. A dates-only call is a real change and writes no entry: "the report went
    out" is a property of the meeting, not a new fact about it.
    """
    root = Path(root)
    src = str(source or "").strip()
    kind = (kind or "").strip().lower()
    facts = [str(f).strip() for f in (facts or []) if str(f).strip()]
    fields = {k: v for k, v in (fields or {}).items() if _as_lines(v)}
    connections = list(connections or [])
    open_questions = _as_lines(open_questions)
    stamps = _dated(dates)
    body_input = bool(facts or fields or connections or open_questions or summary)
    if not body_input and not stamps:
        raise EntityRefused("nothing to record — pass the facts this turn learned, or say nothing new")
    if not src and body_input:
        raise EntityRefused(
            "every fact needs a source: what was said or read that this came from (the meeting, the "
            "mail, the file, the person's own words). A page carries only what was said or read — "
            "if you do not have a source the gap goes to kg/MISSING.md, never onto the page.")

    rel = entity_rel_path(kind, name)
    path = root / rel
    existed = path.exists()
    raw = path.read_text(encoding="utf-8", errors="replace") if existed else ""
    fm, body = split_frontmatter(raw)

    # THE REWRITE HAPPENS BEFORE IDEMPOTENCY IS TESTED, and the order is load-bearing: a fact
    # re-stated next turn arrives as `[[Olga Avramenko]]` and is already stored as
    # `[[ws:k4m…/olga-avramenko]]`, so comparing the raw forms would append it a second time.
    field_lines = {f: _as_lines(v) for f, v in fields.items()}
    flat = list(facts) + [v for vals in field_lines.values() for v in vals] + open_questions
    linked = wikilinks(flat)                        # the names as the caller wrote them
    rewritten_all, cursor = [], 0
    rewritten_flat, rewrites = _rewrite_links(root, flat, name=name, mounts=mounts)
    rewritten_all = rewrites
    facts = rewritten_flat[cursor:cursor + len(facts)]
    cursor += len(facts)
    for f in list(field_lines):
        n = len(field_lines[f])
        field_lines[f] = rewritten_flat[cursor:cursor + n]
        cursor += n
    open_questions = rewritten_flat[cursor:cursor + len(open_questions)]

    resolved, unresolved = resolve_links(root, linked)
    if rewritten_all:
        crossed = {title for title, _ref in rewritten_all}
        resolved += [n for n in unresolved if n in crossed]
        unresolved = [n for n in unresolved if n not in crossed]
    day = _today(today)

    card = parse_card(body)
    # THE SHAPE IS APPLIED HERE, not by the renderer. `render(parse(x)) == x` has to hold for any
    # page — including one a human wrote — so reshaping cannot live on the read path (F98).
    migrated = existed and (migrate_flat(card) or card.was_flat)
    if not card.title:
        card.title = name
    ensure_card_shape(card, kind)

    if not existed:
        fm = [f"type: {kind}", f"id: {slugify(name)}", f"title: {name}",
              f"aliases: {_render_list(aliases)}", f"created: {day}",
              f"sources: {_render_list([s_ for s_ in [src] if s_])}"]
    else:
        for key, value in (("type", kind), ("id", slugify(name)), ("title", name), ("created", day)):
            if _fm_get(fm, key) is None:
                fm = _fm_set(fm, key, value)
        fm = _fm_set(fm, "aliases", _render_list(_list_field(_fm_get(fm, "aliases")) + list(aliases)))
        if src:
            fm = _fm_set(fm, "sources", _render_list(_list_field(_fm_get(fm, "sources")) + [src]))
    new_stamps = {k: v for k, v in stamps.items() if _fm_get(fm, k) != v}
    for key, value in new_stamps.items():
        fm = _fm_set(fm, key, value)

    # ── the body ─────────────────────────────────────────────────────────────────────────────────
    written, filed = 0, {}
    if summary and not card.summary:
        card.summary = summary.strip()
        written += 1

    valid = set(CARD_SECTIONS.get(kind, ()))
    edges = dict(FIELD_LINKS.get(kind, {}))
    for field, values in field_lines.items():
        head = FIELD_SECTION.get(kind, {}).get(field)
        label = FIELD_LABEL.get(field)
        for value in values:
            line = f"{label}: {value}" if label else value
            target = head if head in valid else None
            landed = card.add(target, f"{line} — source: {src}") if target \
                else card.add_timeline(day, f"{line} — source: {src}")
            if landed:
                written += 1
                filed[target or "Timeline"] = filed.get(target or "Timeline", 0) + 1
        if field in edges:
            here, there = edges[field]
            for value in values:
                for target_name in (wikilinks([value]) or [value]):
                    if card.add("Connected", _chip(target_name, here)):
                        written += 1
                    connections.append({"name": target_name, "relation": here, "reverse": there})

    default = section.strip() if section.strip() in valid else ""
    for f in facts:
        landed = card.add(default, f"{f} — source: {src}") if default \
            else card.add_timeline(day, f"{f} — source: {src}")
        if landed:
            written += 1
            filed[default or "Timeline"] = filed.get(default or "Timeline", 0) + 1

    for q in open_questions:
        if card.add("Open questions", q):
            written += 1

    # `## Sources` is DERIVED FROM frontmatter, so it is the one section whose body is not something
    # anybody typed and the one this tool may replace wholesale. One store, one reader.
    card.set_section("Sources", [f"- {s}" for s in _list_field(_fm_get(fm, "sources"))])

    back_links, seen_edges = [], set()
    for c in connections:
        target_name = c["name"] if isinstance(c, dict) else str(c)
        here = (c.get("relation") if isinstance(c, dict) else "") or ""
        there = (c.get("reverse") if isinstance(c, dict) else "") or \
            _RECIPROCAL.get(here.lower(), "") or kind
        if not isinstance(c, dict) or "reverse" not in c:
            if card.add("Connected", _chip(target_name, here)):
                written += 1
        key = (slugify(target_name), there)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        hit = link_back(root, target_name, card.title, there)
        if hit:
            back_links.append(hit)

    if existed and not written and not new_stamps and not migrated:
        return {"path": rel, "created": False, "changed": False, "facts_written": 0,
                "already_recorded": len(facts), "links_resolved": resolved,
                "links_missing": unresolved, "links_rewritten": rewritten_all,
                "kind": kind, "name": name, "dates": {}, "back_links": back_links,
                "migrated": False, "filed": {}}

    drop_empty_tail(card)          # a `## Timeline` that never had an entry helps nobody
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_card(card, kind, fm), encoding="utf-8")
    return {"path": rel, "created": not existed, "changed": True, "facts_written": written,
            "already_recorded": max(0, len(facts) - sum(filed.values())),
            "links_resolved": resolved, "links_missing": unresolved,
            "links_rewritten": rewritten_all, "kind": kind, "name": name, "date": day,
            "dates": new_stamps, "back_links": back_links, "migrated": bool(migrated),
            "filed": filed, "sections": list(CARD_SECTIONS.get(kind, ()))}


# ── candidate names, mechanically (the phase's pre-pass) ─────────────────────────────────────────
#
# THE POINT OF THIS BEING CODE. The write-back phase used to spend a whole model call finding out
# that it had nothing to do — measured at 118-136s on Haiku against a 31-47s answer, which keeps the
# worker busy 3x longer and queues every message the person sends behind it. Extracting the names is
# a regex and a directory listing; only writing the FACTS needs a model. So the cheap half runs
# first, in code, and a turn whose names all already have pages never reaches a model at all.
#
# It is also the SSOT for the measure: `core/flows/eval/dna/score.py` imports this function rather
# than keeping a second regex. Two spellings of "what counts as a name" is how a scorer ends up
# measuring something the product never looked for.

# A capitalised RUN of two or more words — "Sony Pictures Imageworks", "Cottalango Leon". Single
# capitalised words are deliberately not counted: at the start of a sentence every word is one, and
# a rule that fires on "The" and "Monday" is about English, not about the knowledge graph.
#
# `and` and `the` are NOT intra-name particles, and the first version had them: it read "Blue Light
# Card and Kaar Tech" as ONE name, undercounting by exactly the amount a note listing several dead
# names does.
_BARE_NAME = re.compile(r"\b[A-Z][a-zA-Z'’\-]+(?:\s+(?:of|de|del|van|von|da|di)\s+[A-Z][a-zA-Z'’\-]+"
                        r"|\s+[A-Z][a-zA-Z'’\-]+)+")
_FENCE = re.compile(r"```[\s\S]*?```")
_HEADING = re.compile(r"^#{1,6}\s.*$", re.M)
_MD_LINK = re.compile(r"\[[^\]]+\]\([^)]*\)")
_POSSESSIVE = re.compile(r"[’']s$")
_NOT_A_NAME = {"Open Items", "Action Items", "Next Steps", "Open Questions", "Decided Committed"}

# ⚠ MEASURED FALSE POSITIVE. Over ten real DNA notes the extractor flagged "Complete SSO",
# "Ask ASF", "Co-author TAC", "Lead TAC", "Attend SIGGRAPH", "Escalate GitHub", "Await PR" — every
# one a Committed-section bullet, which by convention opens with an imperative verb and is followed
# by an acronym. Those are not names anybody failed to write; counting them inflated the deficit by
# roughly a third and would have sent the phase off to create pages for them.
_LEADING_VERB = {
    "add", "address", "agree", "answer", "ask", "assign", "attend", "await", "book", "bring",
    "build", "can", "check", "circulate", "clarify", "close", "co-author", "complete", "confirm",
    "consider", "continue", "create", "decide", "define", "deliver", "deploy", "did", "discuss",
    "do", "does", "draft", "escalate", "explore", "file", "finalize", "finalise", "find", "fix",
    "follow", "get", "give", "has", "have", "identify", "if", "include", "incorporate",
    "investigate", "invite", "is", "keep", "land", "lead", "let", "look", "make", "merge", "move",
    "open", "organise", "organize", "pick", "plan", "post", "prepare", "present", "propose",
    "provide", "publish", "raise", "reach", "read", "record", "report", "request", "require",
    "resolve", "review", "revisit", "run", "schedule", "send", "set", "share", "should", "start",
    "submit", "support", "take", "test", "that", "the", "these", "this", "those", "track", "update",
    "using", "verify", "wait", "was", "were", "what", "when", "where", "which", "who", "why",
    "will", "work", "would", "write", "your",
}


def candidate_names(text: str, *, mask_linked: bool = True) -> list[str]:
    """Proper names in a piece of prose, in order of appearance, de-duplicated.

    A PROXY, and it says so: it cannot know that "Technical Steering Committee" deserves a page. It
    is a good proxy because it is exactly the shape decision 24 names — a name went past and nothing
    was created — and because the only way to improve the number is to write the page.

    ``mask_linked`` drops names that are already a ``[[wikilink]]`` or a markdown link. TRUE for the
    measure (a linked name is not a failure). FALSE for the phase's pre-pass, because a ``[[Name]]``
    whose page does not exist renders as an inert "not found" chip — the index, not the brackets,
    decides whether a page is there."""
    if not text:
        return []
    fm = _FRONTMATTER.match(text)
    body = text[fm.end():] if fm else text
    body = _FENCE.sub(" ", body)
    body = _HEADING.sub(" ", body)
    if mask_linked:
        body = _WIKILINK.sub(" ", body)
        body = _MD_LINK.sub(" ", body)
    else:
        body = _WIKILINK.sub(r" \1 ", body)
    out: list[str] = []
    for m in _BARE_NAME.finditer(body):
        n = _POSSESSIVE.sub("", " ".join(m.group(0).split())).strip()
        if not n or n in _NOT_A_NAME or n in out:
            continue
        if n.split()[0].lower() in _LEADING_VERB:
            continue
        out.append(n)
    return out


def known_slugs(root) -> set:
    """Every entity slug this workspace already holds — read from `kg/entities/`, never from the
    generated index, because the index can be one write behind and a stale index means a duplicate
    page."""
    base = Path(root) / ENTITIES_DIR
    out = set()
    for kind in KINDS:
        d = base / kind
        if d.is_dir():
            out |= {f.stem for f in d.glob("*.md") if f.name != "index.md"}
    return out


def missing_names(roots, texts, *, limit: int = 8) -> list[str]:
    """The names these texts touched that no mounted desk has a page for, in order, capped.

    This is the whole pre-pass: empty means the phase has nothing to do and no model call is made.
    The cap is here rather than in the prompt because the list IS the budget — a phase handed forty
    names either ignores the cap or runs for four minutes. Eight matches the phase's tool-call cap:
    a list longer than the budget can finish is a plan that gets cut off every single time, and the
    names at the end of it are never the ones anybody chose to drop."""
    known: set = set()
    for r in roots:
        try:
            known |= known_slugs(r)
        except OSError:
            continue
    seen: list[str] = []
    for t in texts:
        for n in candidate_names(t, mask_linked=False):
            slug = slugify(n)
            if slug and slug not in known and n not in seen:
                seen.append(n)
    return _drop_prefixes(seen)[:limit]


def _drop_prefixes(names: list[str]) -> list[str]:
    """Drop a name that is a PREFIX of another one in the same list — the longer spelling wins.

    "James Spad" beside "James Spadafora" is one person and one page, and truncation only ever
    produces the shorter. Deliberately a plain prefix rather than a word-boundary one: the cut that
    matters here lands MID-WORD ("James Spadaf"), which a word-boundary test does not see.

    The trade, stated: "John Smith" in a turn that also names "John Smithson" is dropped, and no
    lexical rule can tell those two cases apart. That costs one page not written this turn — it is
    written the next time the name appears on its own — against a permanent `james-spadaf.md` that
    nothing will ever link to or clean up. Order is preserved, so the first mention still leads."""
    out = []
    for n in names:
        if any(other != n and other.startswith(n) for other in names):
            continue
        out.append(n)
    return out


# ── the index that rides in every dispatch ───────────────────────────────────────────────────────

def _last_updated(text: str, fallback: str) -> str:
    days = _DATED_HEADING.findall(text or "")
    if days:
        return max(days)
    fm, _ = split_frontmatter(text or "")
    return _fm_get(fm, "created") or fallback


def index_rows(root) -> list[tuple[str, str, str, str]]:
    root = Path(root)
    base = root / ENTITIES_DIR
    rows: list[tuple[str, str, str, str]] = []
    for kind in KINDS:
        d = base / kind
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name == "index.md":
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, _ = split_frontmatter(text)
            # `kg/templates/` is not the only place a shape can hide: a doc whose frontmatter says
            # `template: true` is a SHAPE wherever it sits, and the kg-links rule already forbids
            # citing one. Listing it here would put it back in front of the model on every turn.
            if (_fm_get(fm, "template") or "").lower() == "true":
                continue
            title = _fm_get(fm, "title") or f.stem
            try:
                mtime = _dt.date.fromtimestamp(f.stat().st_mtime).isoformat()
            except OSError:
                mtime = ""
            rows.append((kind, title, f"{ENTITIES_DIR}/{kind}/{f.name}", _last_updated(text, mtime)))
    return rows


def render_index(root, slug: str = "") -> str:
    rows = index_rows(root)
    head = f"# Entities in {slug}" if slug else "# Entities"
    if not rows:
        return (head + "\n\nNo entity pages exist here yet. The first name this turn learns something"
                " about gets one — `entity_upsert` creates it in a single call.\n")
    shown, omitted = rows[:INDEX_MAX_ROWS], max(0, len(rows) - INDEX_MAX_ROWS)
    body = [head, "", f"{len(rows)} pages. Regenerated on every `entity_upsert` — never edit by hand.",
            "", "| kind | name | path | last updated |", "|---|---|---|---|"]
    body += [f"| {k} | {t} | `{p}` | {u} |" for k, t, p, u in shown]
    if omitted:
        body.append(f"\n_{omitted} more not listed — read `{ENTITIES_DIR}/` directly for the rest._")
    return "\n".join(body) + "\n"


def write_index(root, slug: str = "") -> str:
    root = Path(root)
    p = root / INDEX_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_index(root, slug), encoding="utf-8")
    return INDEX_PATH


# ── the commit ───────────────────────────────────────────────────────────────────────────────────

def commit_entity(root, paths, *, subject_path: str, created: bool,
                  author=None):
    """ONE commit, BY PATHSPEC, carrying the F31 subject shape ``<workspace>: <path> — added|updated``.

    By pathspec on purpose, and for a standing reason rather than a local nicety: ``git commit``
    commits THE INDEX, so a bare ``add`` + ``commit`` here would sweep in whatever a concurrently
    running worker turn had staged in the same repo and file it under this message. The index is a
    write surface with no owner; naming the paths is how this writer stays in its own lane. The
    subject names the ENTITY page even when `kg/INDEX.md` rides along, because that is the change a
    reader of ``git log --oneline`` came for."""
    root = Path(root)
    if not (root / ".git").is_dir():
        return None
    env = {**os.environ, "GIT_COMMITTER_NAME": "Vexa", "GIT_COMMITTER_EMAIL": "platform@vexa.ai"}
    if author:
        env["GIT_AUTHOR_NAME"], env["GIT_AUTHOR_EMAIL"] = author

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env)

    paths = [str(p) for p in paths if p]
    if not paths:
        return None
    git("add", "--", *paths)
    if not git("diff", "--cached", "--name-only", "--", *paths).stdout.strip():
        return None
    subject = f"{root.name}: {subject_path} — {'added' if created else 'updated'}"[:72]
    if git("commit", "-m", subject, "--", *paths).returncode != 0:
        return None
    return git("rev-parse", "HEAD").stdout.strip() or None
