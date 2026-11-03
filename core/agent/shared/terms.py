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
nothing. ONE ADDITION (2026-10-14): one-word names said mid-sentence ("ask Maria", "Siemens wants")
— see `_one_word_names`. A meeting that names people by first name came back with no terms at all.

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

import datetime as _dt
import re

from workspaces.shared.entities import (KINDS, LEADING_SPEECH_NOISE, candidate_names, slugify,
                                        strip_leading_noise)
from workspaces.shared import entities as _entities

# `kg/entities/<kind>/<stem>.md` — the one shape `entity_upsert` writes and the one shape this
# reads. Anything else in the tree is not an entity page, however much it looks like one.
ENTITIES_PREFIX = "kg/entities/"

# ⚠ MEASURED ON A REAL MEETING, and the reason this layer exists at all.
#
# `candidate_names` was tuned on written NOTES and is right there. Run over the 677 segments of the
# DNA TSC transcript of 2026-03-02 it returned 28 candidates, of which EIGHT were artefacts of
# SPEECH rather than names: "But I'll", "So I'm", "Like I've", "So Cameron", "That's John",
# "And Gabe", "On DNA", "Our TAC". Every one is the same shape — a sentence opens with a
# capitalised function word, the next word is also capitalised, and two capitalised words in a row
# is exactly what the extractor is looking for. Prose does not begin sentences that way; people do,
# constantly, and a transcript is nothing but sentence openings.
#
# So the lead is stripped by the CONSUMER and not in `candidate_names`: that function's behaviour is
# measured against the write-back phase's own numbers (decision 24.4), and quietly changing it to
# suit a different corpus would move a metric nobody was watching.
#
# ONE LIST, ONE OWNER. The noise list and the strip moved to `workspaces.shared.entities`
# (`LEADING_SPEECH_NOISE`, `strip_leading_noise`) when the write-back pre-pass needed the same
# correction: it read "So Vexam, which is the Finos project" out of a transcript and wrote a page
# called "So Vexam". Two copies of this list would drift the first time either was tuned, and the
# drift would show up as a chip for a name the write-back refuses, or the other way round.


# ── one-word names ───────────────────────────────────────────────────────────────────────────────
#
# ⚠ THE DEFECT THIS FIXES (friction, 2026-10-13): `transcript_terms` answered NO terms for a meeting
# that plainly named people and companies. Speech names people by FIRST name ("ask Maria") and
# companies by ONE word ("Siemens wants the pilot"); the two-word floor `candidate_names` holds —
# right for choosing which PAGE to write, measured on notes — saw none of them, so a whole meeting
# came back empty. A chip list is not a page list: a LOOK publishes nothing and the agent chooses
# with `keep`, so a one-word candidate costs one more line in a list, never a chip nobody judged.
#
# The signal is the one an ASR transcript carries reliably: a capital letter in the MIDDLE of a
# sentence. A capital at a sentence's head is grammar, so a word seen only there is not a name —
# unless the same word is also said mid-sentence somewhere in the meeting, and then every line
# that said it counts. Letters are Unicode, as in `entities._WORD`: "Дмитрий" and "Яндекс" are
# names the same way "Maria" is. A script with no letter case (CJK, Arabic) carries no such
# signal and is out of reach of any lexical rule.
_SINGLE_WORD = re.compile(r"[^\W\d_][\w'’\-]*")
_SENTENCE_END = re.compile(r"[.!?…]\s*$")
_POSSESSIVE_TAIL = re.compile(r"[’']s$")

# Words capitalised mid-sentence that name a time, a courtesy or a language, never a thing a page
# would be about. Short on purpose: anything missed is one line in an unpublished list.
_NOT_A_ONE_WORD_NAME = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "ok", "okay", "hi", "hello", "hey", "thanks", "please",
    "sorry", "mr", "mrs", "ms", "dr", "sir", "english", "ai",
}) | LEADING_SPEECH_NOISE | {w.lower() for w in _entities._LEADING_NOT_A_NAME}


def _one_word_names(text: str, covered: set):
    """``(word, mid_sentence)`` for every capitalised single word in ``text`` that is not part of a
    longer name this segment already gave (``covered``) and not a known non-name."""
    for m in _SINGLE_WORD.finditer(text):
        word = _POSSESSIVE_TAIL.sub("", m.group(0).rstrip("'’-"))
        if len(word) < 2 or not word[0].isupper() or word in covered:
            continue
        if word.lower().replace("’", "'") in _NOT_A_ONE_WORD_NAME:
            continue
        head = text[:m.start()]
        yield word, bool(head.strip()) and not _SENTENCE_END.search(head)


def extract_terms(segments) -> list[dict]:
    """The proper names these segments said, in order of first appearance, each with where it was said.

    ``segments`` is any iterable of ``{"id": …, "text": …, "at": …}`` — ``id`` and ``at`` are opaque
    and echoed back; only ``text`` is read.

    A name that is a PREFIX of a longer one MERGES INTO IT rather than being dropped. ``entities``
    drops it, and is right to for its purpose (it is choosing which page to write, and the longer
    spelling is the page). Here the short spelling is a real occurrence in a real line: "James" at
    minute two and "James Hollister" at minute nine are one chip and BOTH lines. Dropping the early
    segment would silently make the chip's provenance start nine minutes after the person did.
    """
    order: list[str] = []
    seen: dict[str, dict] = {}
    heads: list[tuple] = []   # (word, sid, at) seen only at a sentence's head, so far

    def sighting(name, sid, at):
        row = seen.get(name)
        if row is None:
            row = {"term": name, "segments": [], "first_at": at}
            seen[name] = row
            order.append(name)
        if sid is not None and sid not in row["segments"]:
            row["segments"].append(sid)
        return row

    for seg in segments or []:
        text = str((seg or {}).get("text") or "")
        if not text.strip():
            continue
        sid = (seg or {}).get("id")
        at = (seg or {}).get("at")
        covered: set = set()
        for raw in candidate_names(text, mask_linked=False):
            name = strip_leading_noise(raw)
            if not name:
                continue
            covered.update(name.split())
            sighting(name, sid, at)
        for word, mid in _one_word_names(text, covered):
            if mid:
                sighting(word, sid, at)
            else:
                heads.append((word, sid, at))
    # A word said at a sentence's head counts once the meeting has also said it mid-sentence.
    for word, sid, at in heads:
        row = seen.get(word)
        if row is None:
            continue
        sighting(word, sid, at)
        if at is not None and (row["first_at"] is None or str(at) < str(row["first_at"])):
            row["first_at"] = at
    return _merge_prefixes([seen[n] for n in order])


def _merge_prefixes(rows: list[dict]) -> list[dict]:
    """Fold each row whose term is a strict prefix of another row's term into that longer row.

    The trade `entities._drop_prefixes` states holds here too — "John Smith" beside "John Smithson"
    is folded wrongly and no lexical rule can tell those apart. It costs one chip; the alternative
    is two chips for one person, one of which opens a page that will never exist."""
    terms = [r["term"] for r in rows]
    out: list[dict] = []
    for row in rows:
        # The LONGEST match, not the first: with "James" ⊂ "James Holl" ⊂ "James Hollister" a
        # first-match fold would put "James" onto a row that is itself folded away, and its
        # sightings would vanish with it. Folding straight onto the longest spelling makes the
        # chain flat and the operation order-independent.
        # A ONE-WORD term folds only at a word boundary: "Ana" is "Ana Lima", never "Anacorp".
        # A multi-word one keeps the character fold, so ASR's "James Hol" still finds "James
        # Hollister".
        stem = row["term"] if " " in row["term"] else row["term"] + " "
        cands = [t for t in terms if t != row["term"] and t.startswith(stem)]
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


# ── the cursor ───────────────────────────────────────────────────────────────────────────────────

def segment_at(segment: dict):
    """A segment's position on the meeting's clock: its absolute start, else its relative start."""
    return segment.get("absolute_start_time") or segment.get("start")


def _instant(value):
    """``value`` as an aware UTC datetime when it is an ISO-8601 timestamp, else None."""
    text = str(value or "").strip()
    if not text or text.replace(".", "", 1).isdigit():
        return None
    try:
        t = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def segments_since(raw, since) -> list:
    """The segments said AFTER ``since``, the cursor a previous call returned.

    COMPARED AS TIMES when both sides are timestamps. Comparing the strings made the answer depend
    on spelling: ``…14:52:57Z`` sorts after ``…14:52:57.309+00:00`` even though it is earlier, so a
    cursor written with a ``Z`` by one reader hid every later segment from another. Numbers (a
    relative ``start``) compare as numbers. Anything else falls back to the old string order."""
    rows = [g for g in (raw or []) if isinstance(g, dict)]
    if not since:
        return rows
    since_t, since_n = _instant(since), _number(since)

    def after(g) -> bool:
        at = segment_at(g)
        if since_t is not None and _instant(at) is not None:
            return _instant(at) > since_t
        if since_n is not None and _number(at) is not None:
            return _number(at) > since_n
        return str(at or "") > str(since)

    return [g for g in rows if after(g)]


def _number(value):
    try:
        return float(value) if not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def cursor_note(raw, fresh, since) -> str:
    """One sentence that tells an EMPTY READ from a cursor with nothing after it. Both used to come
    back as zero segments, and an agent cannot tell "the meeting said nothing new" from "the
    transcript is empty" without it (friction report, dogfood 2026-10-10)."""
    if not raw:
        return "the transcript has no segments yet"
    if since and not fresh:
        return (f"nothing was said after since={since}; the transcript holds {len(raw)} segments. "
                "Omit since to scan the whole meeting.")
    return ""
