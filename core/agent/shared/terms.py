"""terms.py — the words a meeting said that are worth a chip, and which of them already have a page.

PRD decision 35 (founder, 2026-09-02): *"an mcp tool that will take the current transcript and list
words that should be highlighted and clickable like all the things we have in the docs and chat…
click on a thing and it's dropped into the chat as a research drop — similar to deepen etc — just to
find out what that is."*

MECHANICAL, AND THAT IS THE WHOLE DESIGN. No model call happens in here. The extractor is
``entities.candidate_names`` — the SAME proxy the write-back phase already uses to decide "a name
went past and nothing was created" (decision 24.3) — so a term a chip offers and a page the
write-back phase would write are the same population by construction. Two extractors would have
drifted the first time either was tuned, and the drift would have shown up as a chip that opens
nothing.

``known`` is answered by the ENTITY INDEX of the reader's mounted workspaces, not by a search: the
question is "does a page for this exist where this reader can read it", and the index is exactly
that set. A term matched against someone else's workspace would render a solid chip that 404s for
the person looking at it.

Deliberately a PURE FUNCTION OVER PLAIN DICTS — no HTTP, no redis, no filesystem — for the same
reason ``entities.py`` is: the control-MCP tool composes it out of reads it already makes, agent-api
can mount it unchanged if a route is ever wanted, and the whole thing is offline-provable against a
transcript fixture.

⚠ SEGMENT IDS ARE THE CALLER'S VOCABULARY. Whatever the caller puts in ``id`` comes back in
``segments`` untouched. The gateway's transcript rows and the terminal's live SSE segments do NOT
share an id space, so nothing here may assume one: the client re-finds a term in the text it is
rendering (the ``splitTextIntoSpans`` precedent) and uses its own ids. These are provenance for the
agent, and a cursor for the next call — never a join key across two readers.
"""
from __future__ import annotations

from workspaces.shared.entities import KINDS, candidate_names, slugify

# `kg/entities/<kind>/<stem>.md` — the one shape `entity_upsert` writes and the one shape this
# reads. Anything else in the tree is not an entity page, however much it looks like one.
ENTITIES_PREFIX = "kg/entities/"

# ⚠ MEASURED ON A REAL MEETING, and the reason this layer exists at all.
#
# `candidate_names` was tuned on written NOTES and is right there. Run over the 677 segments of the
# DNA TSC transcript of 2026-03-02 it returned 28 candidates, of which EIGHT were artefacts of
# SPEECH rather than names: "But I'll", "So I'm", "Like I've", "So Cameron", "That's John",
# "And Tommy", "On DNA", "Our TAC". Every one is the same shape — a sentence opens with a
# capitalised function word, the next word is also capitalised, and two capitalised words in a row
# is exactly what the extractor is looking for. Prose does not begin sentences that way; people do,
# constantly, and a transcript is nothing but sentence openings.
#
# So the lead is stripped HERE and not in `candidate_names`: that function's behaviour is measured
# against the write-back phase's own numbers (decision 24.4), and quietly changing it to suit a
# different corpus would move a metric nobody was watching. This is the transcript's own correction,
# named as such.
#
# THE TRADE, STATED: "On Semiconductor" and "Our World in Data" lose their first word and then fall
# to the two-word floor, so they never become chips. That is one chip missed against eight junk
# chips on a single meeting — and a junk chip is not neutral, it is an invitation to research
# "But I'll".
_LEADING_NOISE = frozenset({
    # sentence openers
    "so", "but", "and", "also", "then", "now", "well", "actually", "just", "right", "okay", "ok",
    "yeah", "yes", "no", "oh", "plus", "or", "if", "when", "while", "because", "though", "although",
    "like", "maybe", "anyway", "basically", "honestly", "obviously",
    # demonstratives and possessives that read as part of a name
    "that", "that's", "this", "these", "those", "the", "a", "an",
    "our", "your", "their", "his", "her", "its", "my",
    # pronouns and their contractions — "But I'll" survives the opener strip as "I'll"
    "i", "i'll", "i'm", "i've", "i'd", "we", "we'll", "we're", "we've", "they", "they'll",
    "they're", "they've", "he", "he's", "she", "she's", "it", "it's", "you", "you'll", "you're",
    "you've",
    # prepositions that open a clause
    "on", "in", "at", "for", "to", "with", "from", "by", "as", "of", "about", "into", "over",
})


def _strip_lead(name: str) -> str:
    """Drop leading noise words until the first real one, then hold the two-word floor.

    Repeated rather than single-pass: "And so Cameron" is one utterance and two noise words. What is
    left has to still be a NAME — one word is not, by the extractor's own rule (see the header of
    `extract_terms`) — so a stripped candidate that shrinks to a single word is dropped entirely
    rather than admitted through a back door the two-word floor was closed against."""
    parts = name.split()
    while parts and parts[0].lower() in _LEADING_NOISE:
        parts = parts[1:]
    return " ".join(parts) if len(parts) >= 2 else ""


def extract_terms(segments) -> list[dict]:
    """The proper names these segments said, in order of first appearance, each with where it was said.

    ``segments`` is any iterable of ``{"id": …, "text": …, "at": …}`` — ``id`` and ``at`` are opaque
    and echoed back; only ``text`` is read.

    A name that is a PREFIX of a longer one MERGES INTO IT rather than being dropped. ``entities``
    drops it, and is right to for its purpose (it is choosing which page to write, and the longer
    spelling is the page). Here the short spelling is a real occurrence in a real line: "James" at
    minute two and "James Spadafora" at minute nine are one chip and BOTH lines. Dropping the early
    segment would silently make the chip's provenance start nine minutes after the person did.
    """
    order: list[str] = []
    seen: dict[str, dict] = {}
    for seg in segments or []:
        text = str((seg or {}).get("text") or "")
        if not text.strip():
            continue
        sid = (seg or {}).get("id")
        at = (seg or {}).get("at")
        for raw in candidate_names(text, mask_linked=False):
            name = _strip_lead(raw)
            if not name:
                continue
            row = seen.get(name)
            if row is None:
                row = {"term": name, "segments": [], "first_at": at}
                seen[name] = row
                order.append(name)
            if sid is not None and sid not in row["segments"]:
                row["segments"].append(sid)
    return _merge_prefixes([seen[n] for n in order])


def _merge_prefixes(rows: list[dict]) -> list[dict]:
    """Fold each row whose term is a strict prefix of another row's term into that longer row.

    The trade `entities._drop_prefixes` states holds here too — "John Smith" beside "John Smithson"
    is folded wrongly and no lexical rule can tell those apart. It costs one chip; the alternative
    is two chips for one person, one of which opens a page that will never exist."""
    terms = [r["term"] for r in rows]
    out: list[dict] = []
    for row in rows:
        # The LONGEST match, not the first: with "James" ⊂ "James Spad" ⊂ "James Spadafora" a
        # first-match fold would put "James" onto a row that is itself folded away, and its
        # sightings would vanish with it. Folding straight onto the longest spelling makes the
        # chain flat and the operation order-independent.
        cands = [t for t in terms if t != row["term"] and t.startswith(row["term"])]
        longer = max(cands, key=len) if cands else None
        if longer is None:
            out.append(row)
            continue
        host = next(r for r in rows if r["term"] == longer)
        for sid in row["segments"]:
            if sid not in host["segments"]:
                host["segments"].append(sid)
        # The host inherits the EARLIER first sighting — it is the same thing, first said then.
        if host["first_at"] is None or (row["first_at"] is not None and str(row["first_at"]) < str(host["first_at"])):
            host["first_at"] = row["first_at"]
    # `segments` may now be out of the order they were said in (a fold appends). Sorting is not
    # possible — the ids are opaque — so re-derive order from the ROWS' own order instead: the host
    # keeps its own sightings first, which is the order it saw them.
    return out


def index_entries(workspace_id: str, slug: str, files) -> list[dict]:
    """Every entity page in one workspace's file list, as index rows.

    Reads the TREE, never `kg/INDEX.md`: the index is regenerated after a write and can be one write
    behind, and a stale index means a chip that says "no page yet" about a page the agent wrote
    thirty seconds ago — the exact failure `entities.known_slugs` refuses for the same reason."""
    out: list[dict] = []
    for raw in files or []:
        rel = str(raw or "").strip().lstrip("/")
        if not rel.startswith(ENTITIES_PREFIX) or not rel.endswith(".md"):
            continue
        rest = rel[len(ENTITIES_PREFIX):]
        parts = rest.split("/")
        if len(parts) != 2:
            continue
        kind, fname = parts
        if kind not in KINDS:
            continue
        stem = fname[:-3]
        if not stem or stem == "index":
            continue
        out.append({"workspace_id": workspace_id, "slug": slug, "entity_id": stem,
                    "kind": kind, "path": rel})
    return out


def match_known(terms, index) -> list[dict]:
    """Attach ``known`` (and ``kind`` when the page says one) to each term. Order of ``index`` IS the
    precedence: the caller lists the desk first, then `_global`, then groups, so a name a person has
    written about on their own desk resolves to THEIR page rather than to a namesake in a group."""
    by_slug: dict[str, dict] = {}
    for entry in index or []:
        key = str(entry.get("entity_id") or "")
        if key and key not in by_slug:
            by_slug[key] = entry
    out: list[dict] = []
    for row in terms or []:
        hit = by_slug.get(slugify(row.get("term", "")))
        item = {"term": row.get("term"), "segments": list(row.get("segments") or []),
                "first_at": row.get("first_at"),
                "known": ({"workspace_id": hit.get("workspace_id"), "entity_id": hit.get("entity_id"),
                           "path": hit.get("path")} if hit else None)}
        if hit and hit.get("kind"):
            item["kind"] = hit["kind"]
        out.append(item)
    return out


def terms_for(segments, index) -> list[dict]:
    """The whole thing: what was said → what has a page. The one entry point a caller needs."""
    return match_known(extract_terms(segments), index)
