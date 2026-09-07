"""meeting_terms.py — the ANNOTATION LAYER over a meeting's transcript, kept where a reload finds it.

Founder, 2026-09-06, in a live Google Meet with the transcript canvas open and Highlight pressed
(Vexa-ai/vexa#1595): *"we want transcript being attributed with extracted entities when we get
highlight — it should attribute the transcript in an efficient way (no rewrite)"*.

NO REWRITE IS THE WHOLE DESIGN, and this module is the half that makes it possible. The transcript
is the bot's record and nothing here may touch it. What a Highlight produces is a MAP — surface form
→ entity slug + kind — and the canvas re-finds each surface form in the words it is already drawing
(`splitTextIntoSpans`). So a live meeting keeps attributing NEW segments with no further model call,
a finished one attributes on replay, and one act pays for every segment before and after it.

WHY IT IS ON THE SERVER AT ALL. The chips already reached the screen as a `terms` event on the chat
stream, and that is still how they arrive instantly. But an event is a MOMENT: reload the tab and
the transcript was plain text again, because the only copy was that tab's memory. This is the copy
that outlives the turn — read on open, never remembered by a browser.

APPEND-ONLY, WHICH IS WHAT MAKES A SECOND HIGHLIGHT SAFE. `merge` adds and never removes, keyed on
the lower-cased surface form, so pressing Highlight again an hour into a meeting EXTENDS the map
instead of replacing it, and publishing the same terms twice changes nothing. That idempotence is
what the button's `since` cursor has always assumed; until now nothing held it.

⚠ THIS MODULE AND `clients/terminal/src/canvas/transcriptTerms.ts` MERGE THE SAME MAP and must
agree — the server merges what is STORED, the client merges the stored map with the event that just
arrived. Four rules, stated in both places:

  1. a later answer about `known` WINS, including a later `null` (the page could have been deleted,
     and a solid chip over a page that is gone is the "opens nothing" failure the resolver refuses);
  2. the cursor is always one the SERVER issued — a publish that carries one replaces it, a publish
     that carries none leaves it, and neither side ever invents one;
  3. an empty publish is a NON-EVENT, never an empty map — an empty map would read as "and now there
     are none" and wipe the chips the previous Highlight put there;
  4. order is first-seen, so a chip does not move under the reader between two presses.

`segments` is the ONE field the server unions rather than overwrites, and deliberately: a `since`
scoped publish carries only the sightings from the new stretch of the room, and this file is the
durable provenance of where a term was said. Nothing renders from it — the client re-finds the term
in the text it is drawing — so the union costs nothing and losing it would silently make a chip's
history start at whichever Highlight ran last.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from workspaces.shared.workspace_id import VEXA_DIR

logger = logging.getLogger("agent_api.meeting_terms")

#: Where a meeting's map lives on a desk. Under the dot-dir every enumerator already hides
#: (`scan_workspace_subjects`, the Files tree, `tree_at`) for the same reason `.vexa/workspace.json`
#: and `.vexa/touches.json` are there: it is machinery, never a page somebody opens.
TERMS_DIR = f"{VEXA_DIR}/meeting-terms"

#: Row ids and subjects, as a filename component. Deliberately no `.`, so no arrangement of a
#: caller-supplied id can walk out of the desk — the guard is the alphabet, not a check somebody
#: has to remember to run.
_SAFE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")

#: Bounds. A transcript is unbounded and a publish comes off a caller's JSON, so the file this
#: writes must not be: a room that ran all day and was highlighted twenty times still renders.
MAX_TERMS = 500
MAX_SEGMENTS = 60
MAX_TERM_CHARS = 120


def _key(term) -> str:
    """The identity of a surface form: whitespace-folded and case-insensitive, the same key the
    client's `mergeTerms` uses. "Kaar Tech" published twice, or once as "kaar tech", is one chip."""
    return " ".join(str(term or "").split()).casefold()


def clean_term(row) -> "dict | None":
    """One stored row out of whatever a publisher sent — or None when it is not a term.

    A CLOSED SHAPE, and that is the point of having it: the route takes JSON from a caller and the
    file it lands in is read straight back into a render loop, so keeping exactly the five keys the
    extractor produces (`shared/terms.py`) means nothing else can be smuggled through the store and
    onto somebody's transcript. A one-character term is dropped here for the same reason the client
    drops it in `termSpans` — a span that short is a false match, not a name."""
    term = " ".join(str((row or {}).get("term") or "").split())[:MAX_TERM_CHARS]
    if len(term) < 2:
        return None
    out: dict = {"term": term, "known": None}
    known = (row or {}).get("known")
    if isinstance(known, dict):
        out["known"] = {k: str(known[k]) for k in ("workspace_id", "entity_id", "path")
                        if known.get(k) is not None}
    kind = (row or {}).get("kind")
    if kind:
        out["kind"] = str(kind)[:40]
    segments = (row or {}).get("segments")
    if isinstance(segments, list):
        out["segments"] = [s for s in segments if isinstance(s, (str, int, float))][:MAX_SEGMENTS]
    first_at = (row or {}).get("first_at")
    if isinstance(first_at, (str, int, float)):
        out["first_at"] = first_at
    return out


def merge(stored, published) -> list[dict]:
    """The stored map extended by one publish — rules 1, 3 and 4 of the header, in one place.

    Pure and exported for the same reason the client's `mergeTerms` is: the whole additive promise
    of the Highlight button is this function, and it is the kind of thing that is invisible when
    wrong (chips silently vanishing on the second press)."""
    out = [r for r in (clean_term(r) for r in (stored or [])) if r]
    at: dict = {}
    for i, row in enumerate(out):
        at.setdefault(_key(row["term"]), i)
    for raw in published or []:
        row = clean_term(raw)
        if row is None:
            continue
        i = at.get(_key(row["term"]))
        if i is None:
            if len(out) >= MAX_TERMS:
                continue          # a bound reached is a bound held; never a rotating map
            at[_key(row["term"])] = len(out)
            out.append(row)
            continue
        # `row` always carries `known` (possibly None) — that is rule 1. Every other key is absent
        # when the publisher did not answer it, so the earlier answer survives for free.
        merged = {**out[i], **row}
        seen = out[i].get("segments") or []
        fresh = [s for s in (row.get("segments") or []) if s not in seen]
        if seen or fresh:
            merged["segments"] = (list(seen) + fresh)[:MAX_SEGMENTS]
        out[i] = merged
    return out


def _file(workspaces_root, subject: str, meeting_id) -> "Path | None":
    mid = str(meeting_id or "").strip()
    subj = str(subject or "").strip()
    if not _SAFE.match(mid) or not _SAFE.match(subj):
        return None
    return Path(workspaces_root) / subj / TERMS_DIR / f"{mid}.json"


def read(workspaces_root, subject: str, meeting_id) -> dict:
    """This meeting's map on this person's desk: `{"meeting", "cursor", "terms"}`.

    An EMPTY map is the ordinary answer for a meeting nobody has highlighted, and the caller must be
    able to tell it from a failure: the canvas renders plain text and costs nothing, exactly as it
    did before anybody pressed the button."""
    empty = {"meeting": str(meeting_id or ""), "cursor": "", "terms": []}
    path = _file(workspaces_root, subject, meeting_id)
    if path is None or not path.is_file():
        return empty
    try:
        doc = json.loads(path.read_text(encoding="utf-8") or "null")
    except (OSError, ValueError) as exc:
        # A map we could not read costs the transcript its chips, never its text (P18: say it in the
        # operator channel; the reader is looking at a meeting, not at our filesystem).
        logger.info("could not read the term map at %s: %s", path, exc)
        return empty
    if not isinstance(doc, dict):
        return empty
    return {"meeting": str(meeting_id or ""),
            "cursor": str(doc.get("cursor") or ""),
            "terms": [r for r in (clean_term(r) for r in (doc.get("terms") or [])) if r]}


def extend(workspaces_root, subject: str, meeting_id, published, cursor: str = "") -> dict:
    """Add one Highlight's publish to this meeting's map and return the whole map.

    IDEMPOTENT: the same publish twice leaves the same map, which is what lets the button be pressed
    again without anybody tracking whether it already was. An empty publish writes NOTHING (rule 3)
    — including the cursor, because a cursor that moved without terms would silently skip the
    stretch of room the next Highlight was going to read."""
    path = _file(workspaces_root, subject, meeting_id)
    current = read(workspaces_root, subject, meeting_id)
    if path is None:
        return current
    fresh = [r for r in (clean_term(r) for r in (published or [])) if r]
    if not fresh:
        return current                      # rule 3, cursor included — see the docstring
    doc = {"meeting": str(meeting_id or ""),
           # Rule 2: the cursor is the SERVER's. A publish carrying one replaces it; one carrying
           # none leaves it where it was, so the next Highlight does not re-scan the whole room.
           "cursor": str(cursor or "").strip() or current["cursor"],
           "terms": merge(current["terms"], fresh)}
    if doc == current:
        return current                      # the same publish twice does not even touch the file
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)               # atomic: a reader never sees half a map
        _git_exclude(Path(workspaces_root) / str(subject).strip())
    except OSError as exc:
        logger.info("could not store the term map at %s: %s", path, exc)
        return current
    return doc


def _git_exclude(desk_dir: Path) -> None:
    """Keep the map out of the desk's HISTORY, the way `workspace_ids.mirror_touches` keeps the
    touch log out and for the same reason: `.git/info/exclude` is per-clone and never travels, and
    the worker's post-turn `git add -A` would otherwise commit a new version of this file every
    time somebody pressed Highlight — churn in the history of a person's own desk, for a projection
    of the chat record rather than a fact about the workspace."""
    info = desk_dir / ".git" / "info"
    if not info.parent.is_dir():
        return                              # not a repo (a fresh desk, a test tmpdir) — nothing to exclude
    info.mkdir(parents=True, exist_ok=True)
    ex = info / "exclude"
    body = ex.read_text(encoding="utf-8") if ex.exists() else ""
    if f"/{TERMS_DIR}/" not in body:
        ex.write_text(body.rstrip("\n") + f"\n/{TERMS_DIR}/\n", encoding="utf-8")
