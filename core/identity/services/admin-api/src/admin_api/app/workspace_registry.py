"""The workspace registry's internal doors (MIGRATION-0009, PRD 0.13.4 D5).

Membership, visibility, connections and invites leave the workspace's git tree, where a
contributor's page write made them owner (security review C3), and become rows here:
``workspaces``, ``memberships``, ``invites`` (``schema/models.py``). Identity is their ONE writer;
agent-api, which has no database, reads and writes them over these doors with the internal secret —
the same edge as the membership index. Git keeps a workspace's content only.

The doors speak agent-api's record shapes (a roster row ``{subject, role, added_by, added_at,
email?, …}``, an invite ``{id, hash, role, mode, allowed_emails, expires_at, max_uses, uses,
created_by, created_at, revoked, …}``), so the caller's logic does not change with the store. A
roster or an invite list is replaced WHOLE, in one transaction: agent-api decides under its
per-workspace lock and this records the decision atomically.

Every door is internal-tier with no dev bypass: each reads or writes who may enter a workspace.
"""
from __future__ import annotations

import datetime as _dt
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema.models import DEFAULT_ORG_ID, Invite, Membership, Workspace
from .db import get_db
from .internal_tier import check_internal_no_dev_bypass

ROLES = ("viewer", "contributor", "owner")
VISIBILITIES = ("open", "closed")
KINDS = ("normal", "pair")
WORKSPACE = "workspace:"
#: A CLOSED room's members (0.13.4 S4, D36). An open room's are derived from its workspace's roster
#: by agent-api and never written here.
ROOM = "room:"
ROOM_ROLES = ("owner", "member", "viewer")
_MEMBER_COLUMNS = ("subject", "role", "added_by", "added_at")
_INVITE_COLUMNS = ("id", "hash", "role", "mode", "max_uses", "uses", "expires_at", "created_by",
                   "created_at", "revoked")


def resource(workspace_id: str) -> str:
    return f"{WORKSPACE}{workspace_id}"


def _wid(workspace_id: str) -> str:
    wid = str(workspace_id or "").strip()
    if not wid or len(wid) > 120 or "/" in wid or wid.startswith("."):
        raise HTTPException(400, detail="invalid workspace id")
    return wid


def _member_row(m: Membership) -> dict:
    out = {**(m.data or {}), "subject": m.principal_id, "role": m.role}
    if m.added_by is not None:
        out["added_by"] = m.added_by
    if m.added_at is not None:
        out["added_at"] = m.added_at
    return out


def _invite_row(i: Invite) -> dict:
    return {**(i.data or {}), "id": i.id, "hash": i.token_hash, "role": i.role, "mode": i.mode,
            "max_uses": i.max_uses, "uses": i.uses, "expires_at": i.expires_at,
            "created_by": i.created_by, "created_at": i.created_at, "revoked": bool(i.revoked)}


def _workspace_view(w: Workspace) -> dict:
    return {"id": w.id, "slug": w.slug, "org_id": w.org_id, "owner": w.owner,
            "visibility": w.visibility, "kind": w.kind, "connections": list(w.connections or []),
            "archived_at": w.archived_at.isoformat() if w.archived_at else None}


async def _members(db: AsyncSession, wid: str) -> list[dict]:
    rows = (await db.execute(select(Membership).where(Membership.resource == resource(wid))
                             .order_by(Membership.position, Membership.principal_id))).scalars().all()
    return [_member_row(m) for m in rows]


def build_router() -> APIRouter:
    router = APIRouter(prefix="/internal/registry", include_in_schema=False)

    @router.get("/workspaces/{workspace_id}")
    async def read_workspace(workspace_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        """A workspace's row and roster; 404 when the registry has no row for it."""
        check_internal_no_dev_bypass(request)
        wid = _wid(workspace_id)
        w = await db.get(Workspace, wid)
        if w is None:
            raise HTTPException(404, detail="no such workspace in the registry")
        return {"workspace": _workspace_view(w), "members": await _members(db, wid)}

    @router.put("/workspaces/{workspace_id}")
    async def write_workspace(workspace_id: str, request: Request, body: dict = Body(...),
                              db: AsyncSession = Depends(get_db)):
        """Create or update a workspace. Any of ``members`` (the whole roster, replaced),
        ``visibility``, ``connections``, ``kind``, ``org_id``, ``slug``. The owner column follows the
        roster's first owner."""
        check_internal_no_dev_bypass(request)
        wid = _wid(workspace_id)
        w = await db.get(Workspace, wid, with_for_update=True)
        if w is None:
            w = Workspace(id=wid, slug=str(body.get("slug") or wid), org_id=DEFAULT_ORG_ID,
                          visibility="closed", kind="normal", connections=[])
            db.add(w)
        if "visibility" in body:
            if body["visibility"] not in VISIBILITIES:
                raise HTTPException(400, detail="visibility is open or closed")
            w.visibility = body["visibility"]
        if "kind" in body:
            if body["kind"] not in KINDS:
                raise HTTPException(400, detail="kind is normal or pair")
            w.kind = body["kind"]
        if "connections" in body:
            if not isinstance(body["connections"], list):
                raise HTTPException(400, detail="connections is a list")
            w.connections = [c for c in body["connections"] if isinstance(c, dict)]
        if body.get("org_id"):
            w.org_id = str(body["org_id"])
        if body.get("slug"):
            w.slug = str(body["slug"])
        if "members" in body:
            rows = body["members"]
            if not isinstance(rows, list):
                raise HTTPException(400, detail="members is a list")
            await db.execute(delete(Membership).where(Membership.resource == resource(wid)))
            seen: set[str] = set()
            for pos, r in enumerate(rows):
                if not isinstance(r, dict) or not r.get("subject") or r.get("role") not in ROLES:
                    raise HTTPException(400, detail="each member needs a subject and a role")
                if r["subject"] in seen:
                    continue
                seen.add(r["subject"])
                db.add(Membership(principal_id=str(r["subject"]), resource=resource(wid),
                                  role=r["role"], added_by=r.get("added_by"), added_at=r.get("added_at"),
                                  position=pos,
                                  data={k: v for k, v in r.items() if k not in _MEMBER_COLUMNS}))
            w.owner = next((str(r["subject"]) for r in rows if isinstance(r, dict) and r.get("role") == "owner"), None)
        w.updated_at = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        await db.commit()
        return {"workspace": _workspace_view(w), "members": await _members(db, wid)}

    @router.delete("/workspaces/{workspace_id}")
    async def drop_workspace(workspace_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        """Forget a workspace: its row, roster and invites (un-share, delete)."""
        check_internal_no_dev_bypass(request)
        wid = _wid(workspace_id)
        await db.execute(delete(Membership).where(Membership.resource == resource(wid)))
        await db.execute(delete(Invite).where(Invite.resource == resource(wid)))
        w = await db.get(Workspace, wid)
        existed = w is not None
        if w is not None:
            await db.delete(w)
        await db.commit()
        return {"dropped": existed}

    @router.get("/principals/{principal_id}/memberships")
    async def principal_memberships(principal_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        """Every workspace ``principal_id`` is a member of: ``[{workspace_id, role, added_at}]``."""
        check_internal_no_dev_bypass(request)
        rows = (await db.execute(select(Membership).where(Membership.principal_id == str(principal_id))
                                 .where(Membership.resource.like(f"{WORKSPACE}%")))).scalars().all()
        return {"memberships": [{"workspace_id": m.resource[len(WORKSPACE):], "role": m.role,
                                 "added_at": m.added_at} for m in rows]}

    # ── closed rooms (0.13.4 S4, D36) ─────────────────────────────────────────────────────────
    @router.get("/rooms/{room_id}/members")
    async def read_room(room_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        """A closed room's member rows; 404 when the registry holds none for it (never "nobody")."""
        check_internal_no_dev_bypass(request)
        rid = _wid(room_id)
        rows = (await db.execute(select(Membership).where(Membership.resource == f"{ROOM}{rid}")
                                 .order_by(Membership.position, Membership.principal_id))).scalars().all()
        if not rows:
            raise HTTPException(404, detail="no such room in the registry")
        return {"room_id": rid, "members": [_member_row(m) for m in rows]}

    @router.put("/rooms/{room_id}/members")
    async def write_room(room_id: str, request: Request, body: dict = Body(...),
                         db: AsyncSession = Depends(get_db)):
        """Replace a closed room's member rows whole, in one transaction (agent-api decides)."""
        check_internal_no_dev_bypass(request)
        rid = _wid(room_id)
        rows = body.get("members")
        if not isinstance(rows, list):
            raise HTTPException(400, detail="members is a list")
        await db.execute(delete(Membership).where(Membership.resource == f"{ROOM}{rid}"))
        seen: set[str] = set()
        for pos, r in enumerate(rows):
            if not isinstance(r, dict) or not r.get("subject") or r.get("role") not in ROOM_ROLES:
                raise HTTPException(400, detail="each room member needs a subject and a role")
            if r["subject"] in seen:
                continue
            seen.add(r["subject"])
            db.add(Membership(principal_id=str(r["subject"]), resource=f"{ROOM}{rid}", role=r["role"],
                              added_by=r.get("added_by"), added_at=r.get("added_at"), position=pos,
                              data={k: v for k, v in r.items() if k not in _MEMBER_COLUMNS}))
        await db.commit()
        return {"room_id": rid, "members": len(seen)}

    @router.delete("/rooms/{room_id}/members")
    async def drop_room(room_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        check_internal_no_dev_bypass(request)
        rid = _wid(room_id)
        out = await db.execute(delete(Membership).where(Membership.resource == f"{ROOM}{rid}"))
        await db.commit()
        return {"dropped": bool(out.rowcount)}

    @router.get("/principals/{principal_id}/rooms")
    async def principal_rooms(principal_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        """Every closed room ``principal_id`` is a member of: ``[{room_id, role, added_at}]``."""
        check_internal_no_dev_bypass(request)
        rows = (await db.execute(select(Membership).where(Membership.principal_id == str(principal_id))
                                 .where(Membership.resource.like(f"{ROOM}%")))).scalars().all()
        return {"rooms": [{"room_id": m.resource[len(ROOM):], "role": m.role, "added_at": m.added_at}
                          for m in rows]}

    @router.get("/workspaces/{workspace_id}/invites")
    async def read_invites(workspace_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        check_internal_no_dev_bypass(request)
        wid = _wid(workspace_id)
        rows = (await db.execute(select(Invite).where(Invite.resource == resource(wid))
                                 .order_by(Invite.created_at, Invite.id))).scalars().all()
        return {"invites": [_invite_row(i) for i in rows]}

    @router.put("/workspaces/{workspace_id}/invites")
    async def write_invites(workspace_id: str, request: Request, body: dict = Body(...),
                            db: AsyncSession = Depends(get_db)):
        """Replace a workspace's invite list whole (the caller holds its per-workspace lock)."""
        check_internal_no_dev_bypass(request)
        wid = _wid(workspace_id)
        rows = body.get("invites")
        if not isinstance(rows, list):
            raise HTTPException(400, detail="invites is a list")
        await db.execute(delete(Invite).where(Invite.resource == resource(wid)))
        for r in rows:
            if not isinstance(r, dict) or not r.get("id") or not r.get("hash") or r.get("role") not in ROLES:
                raise HTTPException(400, detail="each invite needs an id, a hash and a role")
            db.add(Invite(id=str(r["id"]), resource=resource(wid), token_hash=str(r["hash"]),
                          role=r["role"], mode=str(r.get("mode") or "open"),
                          max_uses=int(r.get("max_uses") or 1), uses=int(r.get("uses") or 0),
                          expires_at=int(r.get("expires_at") or 0), created_by=r.get("created_by"),
                          created_at=r.get("created_at"), revoked=bool(r.get("revoked")),
                          data={k: v for k, v in r.items() if k not in _INVITE_COLUMNS}))
        await db.commit()
        return {"invites": len(rows)}

    @router.get("/invites/{token_hash}")
    async def invite_by_hash(token_hash: str, request: Request, db: AsyncSession = Depends(get_db)):
        """The invite a token hash names, and its workspace; 404 when none does."""
        check_internal_no_dev_bypass(request)
        i = (await db.execute(select(Invite).where(Invite.token_hash == str(token_hash)))).scalar_one_or_none()
        if i is None or not i.resource.startswith(WORKSPACE):
            raise HTTPException(404, detail="no such invite")
        return {"workspace_id": i.resource[len(WORKSPACE):], "invite": _invite_row(i)}

    return router


__all__ = ["build_router", "resource"]
