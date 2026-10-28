"""The org as a first-class object: who belongs to which org, in what role (MIGRATION-0008).

Identity is the one writer of `orgs` and `org_members` (P23). One org per install for now: every
account's home org is the default org, so every answer here is the answer the install gave before
orgs existed. What this module adds is the SHAPE that later per-org policy, CRM and billing hang on:

- an account's home org is ``users.org_id``; it is a member there without a row (the migration
  writes none), with a DERIVED role: ``admin`` for the instance admin, else ``member``;
- an ``org_members`` row overrides that role, or (later) adds a second org;
- every read here is org-scoped: a viewer's org never answers for another org's people, and an
  org the viewer is not in is answered exactly like one that does not exist (``None``), so a later
  door cannot learn that another org exists or who is in it.

The internal doors (`build_router`) are internal-tier only; nothing calls them yet. One writes:
``POST /internal/users/{id}/personal-org`` makes an account an individual one (its own org of one,
the boundary of its personal CRM).
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema.models import DEFAULT_ORG_ID, PERSONAL_ORG_PREFIX, Org, OrgMember, User
from . import signin_allow
from .db import get_db
from .internal_tier import check_internal_no_dev_bypass, internal_secret

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLES = (ROLE_ADMIN, ROLE_MEMBER)


def home_org(user: User) -> str:
    """The account's home org. A row read before the column existed (or an unsaved object) has none:
    that is the default org, the one every account belonged to before orgs existed."""
    return str(getattr(user, "org_id", None) or DEFAULT_ORG_ID)


def derived_role(user: User) -> str:
    """The role in the home org when no ``org_members`` row says otherwise: the instance admin
    administers the (one) org, everyone else is a member."""
    data = user.data if isinstance(user.data, dict) else {}
    try:
        admin = signin_allow.is_admin(user.email, data, signin_allow.admin_emails()[0])
    except Exception:  # noqa: BLE001 — an unreadable allow-list is "not admin", never a 500
        admin = False
    return ROLE_ADMIN if admin else ROLE_MEMBER


KIND_ORG = "org"
KIND_PERSONAL = "personal"


def org_view(org: Org) -> dict:
    return {"id": org.id, "name": org.name, "kind": org.kind or KIND_ORG,
            "policy_source": org.policy_source, "crm_org_id": org.crm_org_id,
            "billing_account": org.billing_account}


def personal_org_id(user_id: int) -> str:
    return f"{PERSONAL_ORG_PREFIX}{int(user_id)}"


async def make_personal(db: AsyncSession, user: User) -> dict:
    """Make ``user`` an INDIVIDUAL account: its home org becomes its own org of one
    (``p-<id>``, kind ``personal``), which it administers, and whose CRM is its personal CRM.

    Idempotent. Explicit only: nothing calls it on sign-in or on a schedule, and nothing moves an
    account back into a company org (joining one later is a separate, explicit act). The personal
    org's policy source is the install tier, like every org's today."""
    oid = personal_org_id(user.id)
    org = (await db.execute(select(Org).where(Org.id == oid))).scalar_one_or_none()
    if org is None:
        org = Org(id=oid, name=(user.name or user.email or oid)[:200], kind=KIND_PERSONAL,
                  policy_source="_global", data={})
        db.add(org)
        await db.flush()
    row = (await db.execute(select(OrgMember).where(
        OrgMember.org_id == oid, OrgMember.user_id == user.id))).scalar_one_or_none()
    if row is None:
        db.add(OrgMember(org_id=oid, user_id=user.id, role=ROLE_ADMIN))
    user.org_id = oid
    db.add(user)
    await db.commit()
    return {**org_view(org), "role": ROLE_ADMIN}


async def membership(db: AsyncSession, user: User, org_id: str) -> Optional[str]:
    """The account's role in ``org_id``, or None when it is not a member there."""
    row = (await db.execute(select(OrgMember).where(
        OrgMember.org_id == org_id, OrgMember.user_id == user.id))).scalar_one_or_none()
    if row is not None:
        return row.role
    if org_id == home_org(user):
        return derived_role(user)
    return None


async def org_for_viewer(db: AsyncSession, viewer: User, org_id: str) -> Optional[dict]:
    """The org, as the viewer may see it: its record when the viewer is a member, else None —
    the same answer as an org that does not exist."""
    role = await membership(db, viewer, org_id)
    if role is None:
        return None
    org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one_or_none()
    if org is None:
        return None
    return {**org_view(org), "role": role}


async def org_user_ids(db: AsyncSession, org_id: str) -> list[int]:
    """The accounts in one org: home members plus explicit members. Never another org's."""
    explicit = select(OrgMember.user_id).where(OrgMember.org_id == org_id)
    rows = await db.execute(select(User.id).where(
        or_(User.org_id == org_id, User.id.in_(explicit))).order_by(User.id))
    return [int(r[0]) for r in rows.all()]


async def user_in_org(db: AsyncSession, org_id: str, user_id: int) -> Optional[User]:
    """One account, read from inside one org: None when it is not in that org (exactly like an
    account that does not exist)."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or await membership(db, user, org_id) is None:
        return None
    return user


#: crm-api's door (config.v1 capability `personal_crm_erase`). Unset: no CRM, nothing to erase.
CRM_API_URL_ENV = "CRM_API_URL"
ERASE_TIMEOUT_S = 30.0


async def erase_personal(db: AsyncSession, user: User, *, transport=None) -> None:
    """On account delete: erase the person's own CRM (their org of one) through crm-api's door,
    THEN remove the org row (its memberships cascade). Called before the account row goes.

    Fails closed: when a CRM is deployed and does not confirm the erasure, this raises 502 and the
    caller deletes nothing, so a retry is safe and no personal CRM outlives its account. An account
    with no org of one is untouched (nothing is asked)."""
    oid = personal_org_id(user.id)
    org = (await db.execute(select(Org).where(Org.id == oid))).scalar_one_or_none()
    if org is None and home_org(user) != oid:
        return
    base = (os.environ.get(CRM_API_URL_ENV) or "").strip().rstrip("/")
    if base:
        import httpx  # noqa: PLC0415

        try:
            async with httpx.AsyncClient(transport=transport, timeout=ERASE_TIMEOUT_S) as client:
                r = await client.post(f"{base}/internal/orgs/{oid}/erase",
                                      headers={"X-Internal-Secret": internal_secret()})
        except httpx.HTTPError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                detail="the CRM could not erase this person's CRM; nothing was deleted") from exc
        if r.status_code != 200:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                detail="the CRM could not erase this person's CRM; nothing was deleted")
    await db.execute(delete(OrgMember).where(OrgMember.org_id == oid))
    await db.execute(delete(Org).where(Org.id == oid))


def build_router(load_user) -> APIRouter:
    """The read-only internal doors. ``load_user(user_id, db)`` is main's loader (404 on unknown),
    passed in so this module never imports ``main``."""
    router = APIRouter()

    @router.get("/internal/users/{user_id}/org", include_in_schema=False)
    async def internal_user_org(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        """The account's home org and its role there: the org agent-api resolves policy through.
        No dev-mode bypass: it reads one named person by path id (R1801-9)."""
        check_internal_no_dev_bypass(request)
        user = await load_user(user_id, db)
        org_id = home_org(user)
        found = await org_for_viewer(db, user, org_id)
        if found is None:                  # the org row is missing: answer the id, never another org
            found = {"id": org_id, "name": None, "kind": None, "policy_source": None, "crm_org_id": None,
                     "billing_account": None, "role": await membership(db, user, org_id)}
        return {"user_id": user.id, "org": found}

    @router.post("/internal/users/{user_id}/personal-org", include_in_schema=False)
    async def internal_make_personal(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        """Make an account an individual one with its own org of one (and so its personal CRM).
        Internal tier, no dev-mode bypass; explicit and idempotent; nothing calls it yet."""
        check_internal_no_dev_bypass(request)
        user = await load_user(user_id, db)
        return {"user_id": user.id, "org": await make_personal(db, user)}

    @router.get("/internal/orgs/{org_id}", include_in_schema=False)
    async def internal_org(org_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        check_internal_no_dev_bypass(request)
        org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one_or_none()
        if org is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown org")
        return org_view(org)

    return router
