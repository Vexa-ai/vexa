"""principals.py — the workspace principal (PRD 0.13.4 D2; MIGRATION-0009).

A room's turn runs as its WORKSPACE's agent. identity gives each workspace that needs one a numeric
principal of kind ``workspace`` (``schema/models.py`` ``Principal``): its id is a dedicated account
row, so a delegation token for it carries an ordinary numeric ``sub`` and every consumer that keys
on an account id keeps working — while that account owns nothing of any person.

``POST /internal/principals/workspace`` (internal tier, idempotent) is how agent-api gets the
principal for a workspace before it mints a room turn's token: ``{"workspace_id", "bills_to"}`` →
``{"id", "kind", "workspace_id", "bills_to", "status"}``. A workspace keeps one principal; a new
owner moves ``bills_to``. ``GET /internal/principals/{id}`` reads one.

``lookup`` is what ``/internal/validate`` asks when a delegation token names
``principal_kind: "workspace"``: the row must exist, be ``active``, be of kind ``workspace`` and
bill to an account that exists, or the token is refused.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema.models import Principal, User
from .db import get_db
from .internal_tier import check_internal_no_dev_bypass

KIND_PERSON = "person"
KIND_WORKSPACE = "workspace"
STATUS_ACTIVE = "active"
#: The address a workspace principal's account row carries: a reserved, undeliverable domain, so no
#: sign-in link can ever reach it and no provider can map onto it.
PRINCIPAL_EMAIL_DOMAIN = "principal.invalid"


class WorkspacePrincipalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(..., min_length=1, max_length=255)
    bills_to: int = Field(..., ge=1)


def view(p: Principal) -> dict:
    return {"id": p.id, "kind": p.kind, "workspace_id": p.workspace_id, "bills_to": p.bills_to,
            "status": p.status}


def is_principal_account(user: Optional[User]) -> bool:
    """Is this account row a workspace principal's (never a person who may sign in or hold a key)?"""
    data = getattr(user, "data", None)
    return isinstance(data, dict) and data.get("principal_kind") == KIND_WORKSPACE


async def lookup(db: AsyncSession, principal_id: int) -> Optional[Principal]:
    return (await db.execute(select(Principal).where(Principal.id == int(principal_id)))).scalar_one_or_none()


async def ensure_workspace(db: AsyncSession, workspace_id: str, bills_to: int) -> Principal:
    """The workspace's one principal, made on first ask; a different payer moves ``bills_to``."""
    payer = (await db.execute(select(User).where(User.id == int(bills_to)))).scalar_one_or_none()
    if payer is None or is_principal_account(payer):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bills_to names no person's account")
    found = (await db.execute(select(Principal).where(Principal.workspace_id == workspace_id))).scalar_one_or_none()
    if found is not None:
        if found.bills_to != int(bills_to):
            found.bills_to = int(bills_to)
            await db.commit()
        return found
    account = User(email=f"ws-pending-{workspace_id[:180]}@{PRINCIPAL_EMAIL_DOMAIN}".lower(),
                   name=f"workspace {workspace_id}"[:100], max_concurrent_bots=0,
                   data={"principal_kind": KIND_WORKSPACE, "workspace_id": workspace_id})
    db.add(account)
    await db.flush()
    account.email = f"ws-{account.id}@{PRINCIPAL_EMAIL_DOMAIN}"
    principal = Principal(id=account.id, kind=KIND_WORKSPACE, workspace_id=workspace_id,
                          bills_to=int(bills_to), status=STATUS_ACTIVE)
    db.add(principal)
    await db.commit()
    return principal


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/internal/principals/workspace", include_in_schema=False)
    async def internal_workspace_principal(body: WorkspacePrincipalRequest, request: Request,
                                           db: AsyncSession = Depends(get_db)):
        """The workspace's principal, made on first ask. Internal tier, no dev-mode bypass."""
        check_internal_no_dev_bypass(request)
        return view(await ensure_workspace(db, body.workspace_id.strip(), body.bills_to))

    @router.get("/internal/principals/{principal_id}", include_in_schema=False)
    async def internal_principal(principal_id: int, request: Request, db: AsyncSession = Depends(get_db)):
        check_internal_no_dev_bypass(request)
        p = await lookup(db, principal_id)
        if p is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown principal")
        return view(p)

    return router
