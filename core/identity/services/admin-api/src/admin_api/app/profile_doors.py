"""The PUBLIC PROFILE doors (R4, ``public_profile.py``). Every door answers 403 ``feature_disabled``
unless ``public_profiles`` is switched on BY NAME (``VEXA_FEATURES``; ``all`` does not).

    The person, with their own credential:
      GET  /user/profile                      their profile: every field and its public flag, the
                                              state, how many orgs link to it (a count only)
      PUT  /user/profile                      {fields: {name: {value, public}, …}, publish?}
      POST /user/profile/withdraw             hide the whole profile (values kept)
      POST /user/company-verification         {domain} → the TXT record to add
      POST /user/company-verification/check   verified by the TXT record, or an own verified email on it
    Anyone signed in (the directory — no enumeration):
      GET  /user/directory/profiles?q=        ≥ 3 letters, ≤ 10 answers, rate-limited per person
      GET  /user/directory/profiles/{handle}  one published profile, by its opaque handle
    crm-api, with the internal secret (no dev bypass):
      GET  /internal/profiles/{handle}        the public view, read live — never copied
      POST /internal/profiles/{handle}/links  {org, linked} — kept only as a salted hash, for the count
"""
from __future__ import annotations

import os
import secrets
import socket
import struct
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema.models import User
from . import public_profile as pp
from .db import get_db
from .internal_tier import check_internal_no_dev_bypass

LIMITER = pp.RateLimiter(per_minute=int(os.environ.get("VEXA_DIRECTORY_READS_PER_MINUTE", "30") or 30))
PREFIXES = ("/user/profile", "/user/directory/", "/user/company-verification", "/internal/profiles/")


class ProfileEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: dict = Field(default_factory=dict, description="name, photo, role, company, links, bio — each {value, public}")
    publish: Optional[bool] = Field(default=None, description="true publishes, false unpublishes, absent keeps")


class Domain(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(max_length=253)


class LinkChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org: str = Field(min_length=1, max_length=200)
    linked: bool


async def published_rows(db: AsyncSession) -> list:
    """Every row with a published profile, as (id, data, email). Seam for the offline tests."""
    q = select(User.id, User.data, User.email).where(User.data[pp.DATA_KEY]["published"].as_boolean().is_(True))
    return [(r[0], r[1] or {}, r[2]) for r in (await db.execute(q)).all()]


#: The expression `ix_users_public_profile_handle` indexes (schema/models.py), spelled the same.
HANDLE_EXPR = "((data -> 'public_profile') ->> 'handle')"


async def user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """One user by id (the legacy-profile door)."""
    return await db.get(User, user_id)


async def user_by_handle(db: AsyncSession, handle: str, *, for_update: bool = False) -> Optional[User]:
    """The owner of a handle. Reads take no row lock; only the link write does."""
    from sqlalchemy import bindparam, text  # noqa: PLC0415
    q = select(User).where(text(f"{HANDLE_EXPR} = :handle").bindparams(bindparam("handle", handle)))
    if for_update:
        q = q.with_for_update()
    return (await db.execute(q)).scalars().first()


def txt_records(domain: str, timeout: float = 3.0) -> list:
    """The domain's TXT strings, from the system resolver over UDP (stdlib; no extra dependency)."""
    server = os.environ.get("VEXA_DNS_RESOLVER", "")
    if not server:
        try:
            for line in open("/etc/resolv.conf", encoding="utf-8"):
                if line.startswith("nameserver"):
                    server = line.split()[1]
                    break
        except OSError:
            return []
    if not server:
        return []
    qid = secrets.randbelow(0x10000)
    qname = b"".join(bytes([len(p)]) + p.encode("idna") for p in domain.split(".")) + b"\0"
    packet = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + qname + struct.pack(">HH", 16, 1)
    try:
        with socket.socket(socket.AF_INET6 if ":" in server else socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(packet, (server, 53))
            resp = s.recv(4096)
    except OSError:
        return []
    return parse_txt(resp, qid, domain)


def parse_txt(resp: bytes, qid: int, domain: str) -> list:
    """The TXT strings of one DNS answer — only a complete, successful answer to exactly this
    question with this id; anything else (another id, an error code, truncated, malformed) is []."""
    try:
        if len(resp) < 12:
            return []
        rid, flags, qd, an = struct.unpack(">HHHH", resp[:8])
        if rid != qid or not flags & 0x8000 or flags & 0x0200 or flags & 0x000F or qd != 1:
            return []
        i = 12

        def name_at(i: int) -> "tuple[str, int]":
            labels = []
            while True:
                n = resp[i]
                if n == 0:
                    return ".".join(labels), i + 1
                if n & 0xC0 == 0xC0:
                    return ".".join(labels), i + 2
                labels.append(resp[i + 1:i + 1 + n].decode("ascii", "strict").lower())
                i += n + 1
        qname, i = name_at(i)
        qtype, _qclass = struct.unpack(">HH", resp[i:i + 4])
        i += 4
        if qname != domain.lower().rstrip(".") or qtype != 16:
            return []
        out = []
        for _ in range(an):
            _n, i = name_at(i)
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", resp[i:i + 10])
            i += 10
            if i + rdlen > len(resp):
                return []
            if rtype == 16:
                j, parts = i, []
                while j < i + rdlen:
                    n = resp[j]
                    parts.append(resp[j + 1:j + 1 + n].decode("utf-8", "replace"))
                    j += n + 1
                out.append("".join(parts))
            i += rdlen
        return out
    except (IndexError, struct.error, UnicodeDecodeError):
        return []


def build_router(get_person: Callable, get_person_for_update: Callable) -> APIRouter:
    router = APIRouter()

    async def _save(user: User, data: dict, db: AsyncSession) -> None:
        from sqlalchemy.orm import attributes
        user.data = data
        try:
            attributes.flag_modified(user, "data")
        except Exception:  # noqa: BLE001 — a plain object in the offline tests
            pass
        db.add(user)
        await db.commit()

    def _data(user) -> dict:
        return dict(user.data) if isinstance(getattr(user, "data", None), dict) else {}

    # ── RETIRED (PRD 0.13.4 D35, coordinator ruling 2026-10-17): THE profile is the person's claimed
    # CRM record (crm-api `/person/profile`, terminal `/api/person/profile`, public page `/p/<handle>`).
    # One writer: this profile's write path is closed; its reads stay for one release, marked retired,
    # and its values move into the claimed profile when the person claims (`/internal/users/{id}/
    # legacy-profile`, read by crm-api once, on the claim).
    @router.get("/user/profile")
    async def profile_read(user: User = Depends(get_person)):
        """RETIRED, read-only for one release: your old public profile, with where it moved."""
        return {**pp.read_own(_data(user)), "retired": True, "moved_to": pp.MOVED_TO}

    @router.put("/user/profile")
    async def profile_edit(edit: ProfileEdit, user: User = Depends(get_person_for_update)):
        """RETIRED: the profile moved to your claimed record; this write path is closed."""
        raise HTTPException(status.HTTP_410_GONE, detail=pp.moved())

    @router.post("/user/profile/withdraw")
    async def profile_withdraw(user: User = Depends(get_person_for_update)):
        """RETIRED: switch the public page off on your claimed profile instead."""
        raise HTTPException(status.HTTP_410_GONE, detail=pp.moved())

    @router.get("/internal/users/{user_id}/legacy-profile", include_in_schema=False)
    async def legacy_profile(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        """The person's old profile values (their own entries, with the public flags), for crm-api to
        carry into the record they just claimed. Internal tier only; never a public read."""
        check_internal_no_dev_bypass(request)
        try:
            uid = int(user_id)
        except ValueError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")
        owner = await user_by_id(db, uid)
        if owner is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")
        own = pp.read_own(_data(owner))
        return {"handle": own["handle"], "published": own["published"], "fields": own["fields"]}

    @router.post("/user/company-verification")
    async def verify_start(body: Domain, user: User = Depends(get_person_for_update),
                           db: AsyncSession = Depends(get_db)):
        """Start verifying a company domain: add the TXT record this answers, then check."""
        try:
            data = pp.start_verification(_data(user), body.domain)
        except pp.Refused as r:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=r.detail)
        await _save(user, data, db)
        v = data[pp.VERIFY_KEY]
        return {"domain": v["domain"], "txt": v["txt"],
                "how": f"Add a TXT record on {v['domain']} with the value {v['txt']}, then check. "
                       "An email of yours on that domain, once verified, also proves it."}

    @router.post("/user/company-verification/check")
    async def verify_check(user: User = Depends(get_person_for_update), db: AsyncSession = Depends(get_db)):
        data = _data(user)
        v = data.get(pp.VERIFY_KEY) or {}
        if not v.get("domain"):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="start a verification first")
        data = pp.check_verification(data, txt_records=txt_records(v["domain"]),
                                     email=str(getattr(user, "email", "") or ""),
                                     email_verified=pp.email_is_verified(data, str(getattr(user, "email", "") or "")))
        await _save(user, data, db)
        return data[pp.VERIFY_KEY]

    @router.get("/user/directory/profiles")
    async def directory_search(request: Request, q: str = "", user: User = Depends(get_person),
                               db: AsyncSession = Depends(get_db)):
        """Find published profiles by name, role or company (at least 3 letters; at most 10 answers)."""
        if not LIMITER.allow(f"u:{user.id}"):
            return JSONResponse(status_code=429, content={"detail": "too many directory reads — try again in a minute"})
        if len(q.strip()) < pp.MIN_QUERY:
            return {"profiles": []}
        return {"profiles": pp.search(await published_rows(db), q)}

    @router.get("/user/directory/profiles/{handle}")
    async def directory_profile(handle: str):
        """RETIRED: a profile's page is `/p/<handle>` now (the claimed record's public page, which
        keeps the handle a claim carries over). Redirects for one release."""
        if not pp.HANDLE_RE.fullmatch(handle or ""):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such profile")
        # the body says it too: the gateway relays the status and body, not the Location header
        return JSONResponse(status_code=status.HTTP_308_PERMANENT_REDIRECT, headers={"Location": f"/p/{handle}"},
                            content={"retired": True, "moved_to": f"/p/{handle}"})

    @router.get("/internal/profiles/{handle}", include_in_schema=False)
    async def internal_profile(handle: str, request: Request, db: AsyncSession = Depends(get_db)):
        check_internal_no_dev_bypass(request)
        owner = await user_by_handle(db, handle)
        view = pp.public_view(_data(owner), email=getattr(owner, "email", None)) if owner is not None else None
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such profile")
        return view

    @router.post("/internal/profiles/{handle}/links", include_in_schema=False)
    async def internal_link(handle: str, body: LinkChange, request: Request, db: AsyncSession = Depends(get_db)):
        check_internal_no_dev_bypass(request)
        owner = await user_by_handle(db, handle, for_update=True)
        if owner is None or (body.linked and pp.public_view(_data(owner)) is None):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such profile")
        await _save(owner, pp.link(_data(owner), body.org, body.linked), db)
        return {"linked": body.linked}

    return router


def gate(app) -> None:
    """Refuse every profile door with ``feature_disabled`` unless ``public_profiles`` is on by name."""
    @app.middleware("http")
    async def _public_profiles_switch(request: Request, call_next):
        path = request.url.path
        if (path == "/user/profile" or any(path.startswith(p) for p in PREFIXES)) and not pp.feature_on():
            return JSONResponse(status_code=403, content={"detail": pp.refusal()})
        return await call_next(request)
