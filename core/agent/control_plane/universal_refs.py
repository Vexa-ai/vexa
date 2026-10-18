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
REQUESTABLE = ("workspace", "chat", "page", "entity")


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


class RefError(ValueError):
    """Not a reference at all — a 400, with words a person can act on."""


def _answer(r: links.UniversalRef, access: str, *, label=None, open_=None, request=False, **extra) -> dict:
    out = {"kind": r.kind, "id": r.id, "ref": links.ref_chip(r.kind, r.id),
           "url": links.ref_url(r.kind, r.id) if r.kind != "join" else f"/join/{r.id}",
           "access": access, "label": label if access in (READABLE, GRANT) else None,
           "open": open_ if access in (READABLE, GRANT) else None,
           "request_access": bool(request) and access == NOT_YOURS}
    out.update(extra)
    return out


def parse(text: str) -> links.UniversalRef:
    r = links.parse_universal(text)
    if r is None:
        raise RefError("not a reference: give a /r/<kind>/<id> link, a [[kind:id]] chip or a join link")
    return r


def resolve(text: str, seams: Seams) -> dict:
    r = parse(text)
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
