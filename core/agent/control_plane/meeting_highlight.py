"""meeting_highlight.py — what a meeting has NAMED so far, and which of those names has a page.

PRD decision 35: the transcript view has a Highlight button. It posts a silent turn that calls
`transcript_terms` twice — once to LOOK at every candidate, once with `keep=` to PUBLISH the ones
that matter as chips — and the chips paint where the words were said. This module is the
composition behind that verb; the route is `POST /api/meeting/terms/scan`.

MECHANICAL. No model runs here: the extractor is `shared/terms.py` (the same proxy the write-back
phase uses to decide a name went past without a page), matched against the entity index of the
workspaces the CALLER can read, desk first. A term called `known` is a page this reader can open; an
unknown one is a page decision 24 says somebody should be writing.

A LOOK PUBLISHES NOTHING. Chips are on a person's screen, and a list nobody judged is a screen full
of every capitalised word in the room. `keep=` names the ones to publish (`*` for all); those are
added to the meeting's durable map (`meeting_terms.extend`) so a reload paints them again, and the
answer's `emit` is what the harness turns into the chat's `terms` event.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from control_plane import meeting_terms as meeting_terms_mod
from shared import terms as terms_mod
from workspaces.shared import workspace_paths as wpaths

#: The words in `keep` that publish everything the look found.
KEEP_ALL = frozenset({"*", "all"})


def _at(segment: dict):
    return segment.get("absolute_start_time") or segment.get("start")


def entity_files(root: Path) -> list[str]:
    """Every `kg/entities/<kind>/<name>.md` under one workspace root, workspace-relative. The TREE,
    never `kg/INDEX.md`: the index can be one write behind, and a stale index is a chip saying "no
    page yet" about a page written thirty seconds ago."""
    # by descriptor, following no link (``workspace_paths``): a linked kind folder or page is not
    # this workspace's, and its names never become chips here
    return sorted(f"kg/entities/{kind}/{name}"
                  for kind in wpaths.list_dirs_inside(root, "kg/entities")
                  for name in wpaths.list_files_inside(root, f"kg/entities/{kind}", suffix=".md"))


def entity_index(mounts: Iterable[tuple[str, str, Path]]) -> list[dict]:
    """The index rows for ``(workspace_id, slug, path)`` mounts, in the order given — and ORDER IS
    PRECEDENCE: a name this reader wrote about on their own desk resolves to THEIR page, never to a
    namesake in a group they happen to be in."""
    index: list[dict] = []
    seen: set = set()
    for wsid, slug, path in mounts:
        key = Path(path).resolve()
        if key in seen:
            continue
        seen.add(key)
        index += terms_mod.index_entries(wsid, slug, entity_files(Path(path)))
    return index


def scan(segments: list, index: list, *, since: str = "", keep: str = "") -> dict:
    """The look, and — when ``keep`` names any — the publish set. Pure: no I/O."""
    raw = [g for g in (segments or []) if isinstance(g, dict)]
    fresh = terms_mod.segments_since(raw, since)
    lines = [{"id": _at(g), "at": _at(g), "text": str(g.get("text") or "").strip()}
             for g in fresh if str(g.get("text") or "").strip()]
    cursor = str(_at(raw[-1])) if raw else (since or "")
    found = terms_mod.terms_for(lines, index)
    wanted = [w.strip().lower() for w in str(keep or "").split(",") if w.strip()]
    publish_all = any(w in KEEP_ALL for w in wanted)
    said = {str(t.get("term") or "").lower() for t in found}
    emit = (found if publish_all
            else [t for t in found if str(t.get("term") or "").lower() in wanted])
    known = sum(1 for t in found if t.get("known"))
    return {
        "read_ok": True, "cursor": cursor, "since": since or "",
        "scanned_segments": len(lines), "transcript_segments": len(raw),
        "cursor_note": terms_mod.cursor_note(raw, fresh, since), "terms": found,
        "known_count": known, "unknown_count": len(found) - known,
        "emit": emit, "published": len(emit),
        "keep_not_found": [] if publish_all else [w for w in wanted if w not in said],
    }


def highlight(*, root, subject: str, meeting_id: str, segments: list, index: list,
              since: str = "", keep: str = "",
              store: Optional[Callable] = None) -> dict:
    """`scan`, then the publish: the kept terms join the meeting's durable map.

    ``stored`` is None when nothing was published, True when the map took it, and False when the
    chips will paint now and be gone on reload — the STORE failed, never the highlight, and the
    answer says which so the agent never reports a highlight that will not survive."""
    out = {"meeting": str(meeting_id), **scan(segments, index, since=since, keep=keep)}
    stored = None
    if out["emit"]:
        try:
            (store or meeting_terms_mod.extend)(root, subject, str(meeting_id), out["emit"],
                                                out["cursor"])
            stored = True
        except Exception:  # noqa: BLE001 — the chips still paint from the event
            stored = False
    out["stored"] = stored
    out["next"] = (
        "Nothing is on the person's screen yet. Call again with keep=\"<the terms that matter "
        "here, comma separated>\" to publish them as chips — or keep=\"*\" only if genuinely all "
        "of them do." if not out["emit"] else
        f"Published. Say nothing to your person about it — the chips are the answer. Keep "
        f"since={out['cursor']} for the next Highlight on this meeting.")
    out["a_term_with_known_null"] = ("has no page anywhere you can read: `entity_upsert` it when "
                                     "you know what it is.")
    return out
