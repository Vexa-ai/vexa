"""universal_refs.py — what a reference points at, answered for the person asking (#1783).

Founder, 2026-10-14: *"We want references to workspaces, chats, documents, etc., accepted everywhere
where access is confirmed."* The grammar is `workspaces.shared.links` (`/r/<kind>/<id>` and
`[[kind:id|label]]`); this is the ONE resolver behind it. Every surface that shows a reference — a
chip in a chat or a page, a pasted URL, an agent tool argument, a link in a mail or a calendar body
someone opened — asks it, and it answers for the CURRENT viewer:

* ``readable``    — the object's label and where it opens (``open``);
* ``not-yours``   — it exists and this viewer has no claim on it: no label, no name, nothing from
                    inside it; ``request_access`` says whether an owner can be asked;
* ``unavailable`` — the access decision cannot tell "not yours" from "gone" (a meeting: its one
                    access decision answers a row or nothing, by design);
* ``gone``        — nothing by that id;
* ``grant``       — a join link: it lets the holder in, and names what it opens the way its own
                    preview does.

ONE RESOLVER FOR EVERY KIND (PRD 0.13.4 D18, UI 7gg): workspace, room, chat, message, page (and a
heading), file (a page relative to the page it sits in), entity, meeting (and a moment or a line),
card, person, routine, sensor, query, series and join. A reference NEVER GRANTS: each kind is
answered by its object's own access decision, and the anchor (``t=00:12:30``, ``L42``, a heading)
only says where to open what the viewer may already open.

NOTHING THE WRITER PUT IN THE REF IS ECHOED BACK. A chip's `|label` is the writer's hint for a
reader with no server; the answer carries the server's label or none. Otherwise a ref could carry a
private title to a stranger, phrased as a label.

Pure over injected seams (one per kind), so the access states are provable offline; the route
(`routers/refs.py`) wires the real stores.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from workspaces.shared import links

READABLE, NOT_YOURS, GONE, UNAVAILABLE, GRANT = "readable", "not-yours", "gone", "unavailable", "grant"
#: The kinds an owner can be asked about; the others have no single owner the resolver may name.
REQUESTABLE = ("workspace", "chat", "room", "message", "page", "file", "entity")


@dataclass
class Seams:
    """One lookup per kind, each already scoped to the viewer.

    ``workspace(id) -> (record|None, access)``; ``page(workspace_id, target) -> link_resolver answer``;
    ``chat(id) -> (row|None, readable)``; ``meeting(id) -> row|None``; ``flow(id)`` and
    ``sensor(id)`` -> ``(record|None, access)``; ``join(token) -> preview|None``."""
    workspace: Callable
    page: Callable
    chat: Callable
    meeting: Callable
    flow: Optional[Callable] = None
    sensor: Optional[Callable] = None
    join: Optional[Callable] = None
    #: ``message(chat_id, message_id) -> (record|None, readable)``; ``card(id)`` and ``person(id)``
    #: -> ``(record|None, access)``; ``series(id) -> (record|None, access)``.
    message: Optional[Callable] = None
    card: Optional[Callable] = None
    person: Optional[Callable] = None
    series: Optional[Callable] = None
    #: The workspace a ``file:`` reference is relative to (the page it sits in).
    page_workspace: Optional[str] = None


class RefError(ValueError):
    """Not a reference at all — a 400, with words a person can act on."""


def _answer(r: links.UniversalRef, access: str, *, label=None, open_=None, request=False, **extra) -> dict:
    anchor = getattr(r, "anchor", None)
    opened = dict(open_ or {}) if access in (READABLE, GRANT) else None
    if opened is not None and anchor:
        opened["anchor"] = anchor
    out = {"kind": r.kind, "id": r.id, "ref": links.ref_chip(r.kind, r.id, anchor=anchor),
           "url": links.ref_url(r.kind, r.id, anchor=anchor) if r.kind != "join" else f"/join/{r.id}",
           "access": access, "label": label if access in (READABLE, GRANT) else None,
           "open": opened if opened else None,
           "request_access": bool(request) and access == NOT_YOURS}
    if anchor:
        out["anchor"] = anchor
    out.update(extra)
    return out


def _query_label(spec: str) -> str:
    """A query's label is its own text, tidied: it carries nothing the reference does not."""
    what, _, rest = spec.partition("?")
    words = what.replace("-", " ").replace("_", " ").strip() or "list"
    return f"{words[:1].upper()}{words[1:]}" + (f" ({rest.replace('&', ', ')})" if rest else "")


def parse(text: str) -> links.UniversalRef:
    r = links.parse_universal(text)
    if r is None:
        raise RefError("not a reference: give a /r/<kind>/<id> link, a [[kind:id]] chip or a join link")
    return r


def resolve(text: str, seams: Seams) -> dict:
    r = parse(text)
    if r.kind == "room":
        r = links.UniversalRef("chat", r.id, r.label, r.anchor)
        out = resolve_ref(r, seams)
        return {**out, "kind": "room", "ref": links.ref_chip("room", r.id, anchor=r.anchor),
                "url": links.ref_url("room", r.id, anchor=r.anchor)}
    return resolve_ref(r, seams)


def resolve_ref(r: links.UniversalRef, seams: Seams) -> dict:
    if r.kind == "file":
        if not seams.page_workspace or not r.id:
            return _answer(r, GONE)
        a = seams.page(seams.page_workspace, r.id)
        access = a.get("access") or GONE
        if access == READABLE and a.get("missing"):
            access = GONE
        return _answer(r, access, label=a.get("title"),
                       open_={"path": a.get("path"), "slug": a.get("slug")}, request=True)
    if r.kind == "message":
        cid, _, mid = r.id.partition("/")
        if not mid or seams.message is None:
            return _answer(r, GONE if not mid else UNAVAILABLE)
        rec, readable = seams.message(cid, mid)
        if rec is None:
            return _answer(r, GONE)
        if not readable:
            return _answer(r, NOT_YOURS, request=True)
        title = (rec.get("chat") or {}).get("title") or "a chat"
        return _answer(r, READABLE, label=f"Message in {title}", open_={"chat": cid, "message": mid})
    if r.kind in ("card", "person", "series"):
        seam = {"card": seams.card, "person": seams.person, "series": seams.series}[r.kind]
        if seam is None:
            return _answer(r, UNAVAILABLE)
        rec, access = seam(r.id)
        label = (rec or {}).get("title") or (rec or {}).get("name")
        return _answer(r, access, label=label, open_={r.kind: r.id})
    if r.kind == "query":
        # a query is a description of a list: each viewer opens THEIR OWN version of it (7ff)
        return _answer(r, READABLE, label=_query_label(r.id), open_={"query": r.id})
    if r.kind == "routine":
        r = links.UniversalRef("flow", r.id, r.label, r.anchor)
        out = resolve_ref(r, seams)
        return {**out, "kind": "routine"}
    if r.kind == "workspace":
        rec, access = seams.workspace(r.id)
        return _answer(r, access, label=(rec or {}).get("name") or (rec or {}).get("slug"),
                       open_={"workspace": (rec or {}).get("slug")}, request=bool((rec or {}).get("owner")))
    if r.kind in ("page", "entity"):
        wid, _, target = r.id.partition("/")
        if not target:
            return _answer(r, GONE)
        a = seams.page(wid, target)
        access = a.get("access") or GONE
        if access == READABLE and a.get("missing"):
            access = GONE
        return _answer(r, access, label=a.get("title"),
                       open_={"path": a.get("path"), "slug": a.get("slug")}, request=True)
    if r.kind == "chat":
        row, readable = seams.chat(r.id)
        if row is None:
            return _answer(r, GONE)
        return _answer(r, READABLE if readable else NOT_YOURS, label=row.get("title") or "Untitled chat",
                       open_={"chat": r.id}, request=True)
    if r.kind == "meeting":
        try:
            mid = int(r.id)
        except ValueError:
            return _answer(r, GONE)
        row = seams.meeting(mid)
        if not row:
            return _answer(r, UNAVAILABLE)
        return _answer(r, READABLE, label=row.get("title") or row.get("native_meeting_id") or f"Meeting {mid}",
                       open_={"meeting": mid})
    if r.kind in ("flow", "sensor"):
        seam = seams.flow if r.kind == "flow" else seams.sensor
        if seam is None:
            return _answer(r, UNAVAILABLE)
        rec, access = seam(r.id)
        return _answer(r, access, label=(rec or {}).get("name"), open_={r.kind: r.id})
    if r.kind == "join":
        p = seams.join(r.id) if seams.join else None
        if not p:
            return _answer(r, GONE)
        return _answer(r, GRANT, label=p.get("name"), open_={"join": r.id},
                       target={"kind": p.get("target_kind"), "id": p.get("target_id")})
    return _answer(r, GONE)
