"""meeting_doc.py — THE MEETING'S OWN PAGE: one document, with the live transcript in it, grown by
Expand reading only what has been said since last time.

Founder, 2026-09-06, in a live meeting (Vexa-ai/vexa#1598):

    *"how can we have a meeting artefact that is being updated on meeting on person clicking expand?
    that would read transcript. Essentially this just means doc reading transcript which we have and
    we want this doc to open alongside transcript as a single thing in the right side so it's a kind
    of doc that has live transcript widget in it"*

So there is ONE page on the right, not two tabs: the meeting doc, with the transcript embedded as a
widget. This module owns that document's SHAPE — the marker the widget renders from, the regions the
agent rewrites, and the cursor that makes a second Expand incremental. It decides nothing about
CONTENT: what a section says is the agent's, what the report says is the flow's.

── THE THREE MECHANICAL PARTS ───────────────────────────────────────────────────────────────────

1. **THE WIDGET SLOT** — ``<!-- vexa:transcript meeting=147 -->``. An HTML comment, so the file stays
   plain markdown for every other reader: GitHub, Obsidian, the mail that carries the report, the
   next agent that reads it as text. The terminal splits on it and renders the live transcript in
   place (``clients/terminal/src/ui-kit/transcriptSlot.ts`` — the same spelling, pinned by
   ``gate:fact-parity``). **Nothing here ever writes inside it or moves it**: it is a hole in the
   page, and a regenerating writer that steps on it takes the live transcript off the screen.

2. **REGIONS** — ``<!-- meeting:decisions:start --> … <!-- meeting:decisions:end -->``, exactly the
   desk README's pattern (``shared/desk_readme.py``) and for exactly its reason: *a section a human
   edits needs a fence around it more than a generated one does — the generator has to know where to
   stop.* Text outside every region is HAND-WRITTEN and is never touched, not by an Expand, not by
   the flow's report, not by a second run of either.

3. **THE CURSOR** — ``transcript_cursor`` in the frontmatter. Expand reads the transcript with
   ``since=<cursor>`` and advances it, so the second press costs the model what has been SAID since
   the first, not the whole room again. The value is opaque here on purpose: it is whatever the
   transcript reader hands back (today the meetings domain's ``absolute_start_time``, an ISO
   timestamp, which is what ``meeting_transcript`` and ``transcript_terms`` both return as
   ``cursor``). This module never invents one, never parses one, and only ever advances it —
   see ``advance_cursor``: a cursor that could move BACKWARDS would re-feed the model a stretch of
   room it has already written up, and the second write-up is the one that reads like a stranger.

── WHY THE LOGIC IS HERE AND NOT IN THE ASK ─────────────────────────────────────────────────────

The act itself is a model turn (``behavior/asks/extend-meeting.md``). A preset can *tell* an agent
to preserve a hand-written paragraph, and most turns will; the one that does not costs somebody
their own writing, silently, and nobody finds out until they look. So the preset says WHAT to write
and this module decides WHERE it lands — the agent hands over a region body and gets back a whole
document with everything else exactly as it was.

Stdlib only, and no imports from the rest of this tree: the same file is read by agent-api, by the
worker, by the dogfood rig (``deploy/dogfood/rig/vexa_control_mcp.py`` loads ``shared.*`` by path)
and by the tests. A dependency here would be a dependency in four images.
"""
from __future__ import annotations

import re

__all__ = [
    "CURSOR_KEY", "REGIONS", "SLOT_SOURCE",
    "advance_cursor", "has_slot", "ensure_slot", "read_cursor", "read_region",
    "region_markers", "scaffold", "slot_marker", "slot_meeting", "write_region",
]

#: The widget slot, as the terminal reads it. ONE spelling, two languages — the TypeScript copy is
#: `TRANSCRIPT_SLOT_SOURCE` in `clients/terminal/src/ui-kit/transcriptSlot.ts` and `gate:fact-parity`
#: compares them, because a marker that drifts by one space renders as nothing and reports nothing.
SLOT_SOURCE = r"""<!--\s*vexa:transcript\s+meeting=["']?([A-Za-z0-9_.:-]{1,128})["']?\s*-->"""

_SLOT = re.compile(SLOT_SOURCE)

#: The frontmatter key that makes a second Expand incremental.
CURSOR_KEY = "transcript_cursor"

#: The regenerated regions, in page order. Keys are the vocabulary the ask speaks; the heading is
#: what a reader sees. A key not in this tuple is refused rather than created — an agent that
#: invents `meeting:thoughts:` would grow a page nothing else can find, update or reconcile.
REGIONS: tuple[tuple[str, str], ...] = (
    ("about", "What this is about"),
    ("decisions", "Decisions"),
    ("commitments", "Commitments"),
    ("people", "People and companies"),
    ("questions", "Open questions"),
    # THE FLOW'S HALF. `drop_to_attendees` writes the post-meeting report into this one, so the
    # report and the live notes are the same document rather than two files with one subject.
    ("report", "Report"),
)

_REGION_KEYS = frozenset(k for k, _ in REGIONS)

_MARKER = "<!-- meeting:{key}:{edge} -->"

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)


# ── the widget slot ──────────────────────────────────────────────────────────────────────────────

def slot_marker(meeting: str) -> str:
    """The marker for one meeting. The only place this string is composed."""
    return f"<!-- vexa:transcript meeting={str(meeting or '').strip()} -->"


def has_slot(text: str) -> bool:
    return bool(_SLOT.search(text or ""))


def slot_meeting(text: str) -> str:
    """Which meeting this page's widget is bound to, or ``""``."""
    m = _SLOT.search(text or "")
    return m.group(1) if m else ""


def ensure_slot(text: str, meeting: str) -> str:
    """The document with its transcript widget present — added right under the title when it is not.

    IDEMPOTENT, and deliberately blind to WHICH meeting a present slot names: a page that already
    declares a widget has been decided about, and silently repointing somebody's page at a different
    room is the kind of helpful rewrite this whole module exists to refuse. A page pointing at the
    wrong meeting is a visible defect somebody can fix; a page that quietly changed rooms is not."""
    body = text or ""
    if not str(meeting or "").strip() or has_slot(body):
        return body
    marker = slot_marker(meeting)
    fm, rest = _split_frontmatter(body)
    lines = rest.split("\n")
    # under the first heading if there is one — the widget belongs with the meeting's name, not
    # above it — and otherwise at the very top of the body.
    at = 0
    for i, ln in enumerate(lines):
        if ln.startswith("#"):
            at = i + 1
            break
    head, tail = lines[:at], lines[at:]
    while tail and not tail[0].strip():
        tail.pop(0)
    merged = "\n".join(head + ["", marker, ""] + tail).lstrip("\n")
    return _join_frontmatter(fm, merged)


# ── the regions ──────────────────────────────────────────────────────────────────────────────────

def region_markers(key: str) -> tuple[str, str]:
    """``(start, end)`` for one region. Raises on a key that is not in ``REGIONS``."""
    k = _checked(key)
    return _MARKER.format(key=k, edge="start"), _MARKER.format(key=k, edge="end")


def read_region(text: str, key: str) -> str | None:
    """What a region currently holds, or ``None`` when the document has no such region.

    ``None`` and ``""`` are different answers and both are real: no region at all, versus a region
    an Expand emptied. A caller that conflates them appends a second copy of a section every run."""
    start, end = region_markers(key)
    body = text or ""
    i, j = body.find(start), body.find(end)
    if i == -1 or j == -1 or j < i:
        return None
    return body[i + len(start):j].strip("\n")


def write_region(text: str, key: str, content: str, *, heading: bool = True) -> str:
    """The document with ONE region replaced (or appended, with its heading, when absent).

    Everything else comes back byte for byte: the hand-written paragraphs, the other regions, the
    frontmatter, and — the one that matters most — the widget slot. A region is appended at the END
    of the document rather than anywhere clever, because the alternative is a writer deciding where
    somebody else's page should be reorganised to."""
    k = _checked(key)
    start, end = region_markers(k)
    body = text or ""
    inner = (content or "").strip("\n")
    i, j = body.find(start), body.find(end)
    if i != -1 and j != -1 and j >= i:
        return body[: i + len(start)] + "\n" + inner + "\n" + body[j:]
    title = dict(REGIONS)[k]
    block = (f"## {title}\n" if heading else "") + f"{start}\n{inner}\n{end}"
    return (body.rstrip("\n") + "\n\n" + block + "\n") if body.strip() else block + "\n"


# ── the cursor ───────────────────────────────────────────────────────────────────────────────────

def read_cursor(text: str) -> str:
    """This page's transcript cursor, or ``""`` — which is the honest answer for a page nobody has
    expanded yet, and the value the transcript reader itself treats as "from the beginning"."""
    fm, _ = _split_frontmatter(text or "")
    for ln in fm:
        k, sep, v = ln.partition(":")
        if sep and k.strip() == CURSOR_KEY:
            return v.split("#")[0].strip().strip("'\"")
    return ""


def advance_cursor(text: str, cursor: str) -> str:
    """The document with its cursor moved to ``cursor`` — FORWARD ONLY.

    A cursor that went backwards would hand the next Expand a stretch of the room it has already
    written up, and the page would grow a second account of the same ten minutes in a slightly
    different voice. Comparison is the string compare the transcript reader itself uses on these
    values (`meeting_transcript`: *"String compare is right for ISO timestamps and for the
    float-seconds the gateway also emits, as long as both sides come from _at"*) — so this module
    stays out of the business of parsing a cursor it did not mint.

    An empty `cursor` is a no-op: a read that returned nothing has not moved the meeting on."""
    new = str(cursor or "").strip()
    if not new:
        return text or ""
    if new <= read_cursor(text or ""):
        return text or ""
    fm, rest = _split_frontmatter(text or "")
    out, done = [], False
    for ln in fm:
        k, sep, _v = ln.partition(":")
        if sep and k.strip() == CURSOR_KEY and not done:
            out.append(f"{CURSOR_KEY}: {new}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"{CURSOR_KEY}: {new}")
    return _join_frontmatter(out, rest)


# ── a page that is not there yet ─────────────────────────────────────────────────────────────────

def scaffold(*, meeting: str, title: str = "", native: str = "", date: str = "") -> str:
    """A fresh meeting doc: frontmatter, the title, the widget, and the empty regions.

    The regions are created EMPTY rather than filled with a placeholder sentence. A page that says
    "No decisions yet" in six sections reads as a form somebody has to fill in; the empty region is
    invisible until an Expand puts something in it."""
    name = str(title or "").strip() or "Meeting"
    fm = ["type: meeting"]
    if str(meeting or "").strip():
        fm.append(f"meeting: {str(meeting).strip()}")
    if str(native or "").strip():
        fm.append(f"native: {str(native).strip()}")
    fm.append(f"title: {name}")
    if str(date or "").strip():
        fm.append(f"date: {str(date).strip()}")
    fm.append(f"{CURSOR_KEY}:")
    doc = "---\n" + "\n".join(fm) + "\n---\n\n" + f"# {name}\n\n" + slot_marker(meeting) + "\n"
    for key, _title in REGIONS:
        doc = write_region(doc, key, "")
    return doc


# ── frontmatter, kept RAW ────────────────────────────────────────────────────────────────────────

def _split_frontmatter(text: str) -> tuple[list[str], str]:
    """``(frontmatter lines, body)``, lines kept verbatim — comments, ordering, keys this module has
    never heard of. A page a human edited comes back out the way they left it. Same contract as
    `workspaces.shared.entities.split_frontmatter`, re-implemented rather than imported so this
    module can be dropped into any image with no import graph at all."""
    m = _FRONTMATTER.match(text or "")
    if not m:
        return [], text or ""
    return m.group(1).splitlines(), (text or "")[m.end():]


def _join_frontmatter(lines: list[str], body: str) -> str:
    if not lines:
        return body
    return "---\n" + "\n".join(lines) + "\n---\n" + ("" if body.startswith("\n") else "\n") + body.lstrip("\n")


def _checked(key: str) -> str:
    k = str(key or "").strip().lower()
    if k not in _REGION_KEYS:
        raise ValueError(f"{k!r} is not a meeting-doc region; the set is {sorted(_REGION_KEYS)}")
    return k
