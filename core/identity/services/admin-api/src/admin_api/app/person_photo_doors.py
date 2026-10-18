"""The doors to a person's photo (Vexa-ai/vexa#1783, UI 15g). The rules are `person_photos.py`.

The person's own, through the gateway (identity `routes.v1`), with their own credential:

    GET    /user/photo         which photo shows for them and where it came from (never the bytes)
    PUT    /user/photo         {"image": "<base64 PNG|JPEG|WebP, ≤200 KB>"} — their upload
    DELETE /user/photo         remove their upload; the provider photo or Gravatar shows again

A worker's delegation token is REFUSED on the two writes, whatever its regime: only the person
changes their own face. An agent may suggest a photo and say how; it never sets one. (The edge
refuses the token on these rows too: they carry neither `delegation` nor `mcp_reentry`.)

The internal tier, ``INTERNAL_API_SECRET`` with no dev-mode bypass (the person is named in the path):

    GET  /internal/users/{id}/photo         the bytes agent-api serves, after IT has checked that the
                                            viewer shares a workspace or a chat with this person
    PUT  /internal/users/{id}/signin-photo  {"provider", "id_token" | "access_token"} — what one
                                            sign-in carried. Google / OIDC: the raw ID token, which
                                            identity verifies (`idp_tokens`) and whose subject must be
                                            the account's bound identity; `picture` is read from it
                                            and fetched through the outbound guard. Microsoft: Graph
                                            with the sign-in's own token, only for the bound account.
                                            No url, no bytes: no caller names or hands identity a
                                            picture to put on somebody.

The bytes are in the object store (`photo_storage.py`), the row in `person_photos`. With no object
store configured a photo cannot be set (503) and none is served (404 — initials).

Identity never decides who may SEE a photo: it does not know who shares a workspace with whom.
agent-api does, and asks for the bytes only after it said yes.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .. import delegation as delegation_mod
from ..schema.models import PersonPhoto, User
from . import idp_tokens
from . import person_photos as pp
from . import photo_storage
from .db import get_db
from .internal_tier import check_internal_no_dev_bypass

logger = logging.getLogger("admin_api.person_photos")

#: Operator switch for the Gravatar check at sign-in (config.v1 `VEXA_PERSON_GRAVATAR`).
GRAVATAR_ENV = "VEXA_PERSON_GRAVATAR"

PERSON_ONLY_REFUSAL = ("a person's photo is changed by that person only, in their own settings — "
                       "suggest it to them; a worker acting for them does not set it")


def gravatar_enabled() -> bool:
    return (os.getenv(GRAVATAR_ENV) or "true").strip().lower() not in ("false", "off", "no", "0")


class DbStore:
    """`person_photos.Store`: the row in `person_photos`, the bytes in the object store."""

    def __init__(self, db: AsyncSession, objects=None) -> None:
        self.db = db
        self.objects = objects if objects is not None else photo_storage.from_env()

    async def rows(self, user_id: int) -> Dict[str, dict]:
        got = (await self.db.execute(select(PersonPhoto).where(PersonPhoto.user_id == user_id))).scalars().all()
        return {r.source: {"object_key": r.object_key or "", "content_type": r.content_type or "",
                           "sha256": r.sha256 or "", "origin": r.origin or "", "provider": r.provider or "",
                           "checked_at": float(r.checked_at or 0), "updated_at": float(r.updated_at or 0)}
                for r in got}

    async def put(self, user_id: int, source: str, meta: dict, body: Optional[bytes]) -> None:
        old = (await self.rows(user_id)).get(source) or {}
        key = ""
        if body is not None:
            if self.objects is None:
                raise pp.Refused("photos need the object store, and none is configured here")
            key = photo_storage.key_for(user_id, source, meta["sha256"], meta["content_type"])
            self.objects.put(key, body, meta["content_type"])
        await self.db.execute(delete(PersonPhoto).where(PersonPhoto.user_id == user_id,
                                                        PersonPhoto.source == source))
        self.db.add(PersonPhoto(user_id=user_id, source=source, object_key=key,
                                content_type=meta.get("content_type") or "", sha256=meta.get("sha256") or "",
                                origin=meta.get("origin") or "", provider=meta.get("provider") or "",
                                checked_at=float(meta.get("checked_at") or 0),
                                updated_at=float(meta.get("updated_at") or 0)))
        await self.db.commit()
        self._forget(old.get("object_key"), keep=key)

    async def drop(self, user_id: int, source: str) -> None:
        old = (await self.rows(user_id)).get(source) or {}
        await self.db.execute(delete(PersonPhoto).where(PersonPhoto.user_id == user_id,
                                                        PersonPhoto.source == source))
        await self.db.commit()
        self._forget(old.get("object_key"))

    async def read(self, user_id: int, source: str, meta: dict) -> Optional[bytes]:
        key = meta.get("object_key") or ""
        if not key or self.objects is None:
            return None
        try:
            return self.objects.get(key)
        except Exception:  # noqa: BLE001 — an unreachable store shows initials, never an error page
            logger.warning("person photo read failed for user %s (%s)", user_id, source)
            return None

    def _forget(self, key: Optional[str], keep: str = "") -> None:
        if key and key != keep and self.objects is not None:
            try:
                self.objects.delete(key)
            except Exception:  # noqa: BLE001 — an orphan is removed with the person, never kept
                logger.warning("person photo object not deleted: %s", key)


class PhotoUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(..., max_length=280_000,
                       description="The photo as base64 (a data: URL is accepted): PNG, JPEG or WebP, at most 200 KB.")


class SigninPhoto(BaseModel):
    """What one sign-in carried. NO BYTES: identity fetches the picture itself. ``extra="forbid"``
    refuses a `data` field (or anything else) with 422."""
    model_config = ConfigDict(extra="forbid")
    provider: str = Field("", max_length=16, description="google, microsoft, oidc — or empty for an emailed link")
    id_token: str = Field("", max_length=16384, description="Google / OIDC: the sign-in's raw ID token; identity "
                          "verifies it and reads `picture` from it. There is no url field: no caller names the picture")
    access_token: str = Field("", max_length=8192, description="Microsoft: the sign-in's own access token, used "
                              "for two Graph requests (who is this, their photo) and never stored")


def _delegated(request: Request) -> bool:
    key = request.headers.get("X-API-Key") or ""
    return bool(key) and delegation_mod.is_delegation_token(key)


def build_router(get_current_person: Callable, *, store_of: Optional[Callable] = None,
                 fetch: Optional[Callable] = None) -> APIRouter:
    """The photo doors. ``store_of(db)`` and ``fetch`` are seams for the offline tests."""
    router = APIRouter()
    make_store = store_of or DbStore
    outbound = fetch or pp.fetch_image

    def _own(user: User) -> int:
        return int(user.id)

    @router.get("/user/photo")
    async def get_own_photo(user: User = Depends(get_current_person), db: AsyncSession = Depends(get_db)):
        """The person's own photo: which source shows (upload, provider, gravatar, or none — then a
        monogram), and their account id, so the client can show it."""
        return {"subject": str(user.id), **pp.describe(await make_store(db).rows(_own(user)))}

    @router.put("/user/photo")
    async def put_own_photo(body: PhotoUpload, request: Request,
                            user: User = Depends(get_current_person), db: AsyncSession = Depends(get_db)):
        """Set THEIR OWN photo. The person only — a worker acting for them is refused."""
        if _delegated(request):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"refused": "the photo is the person's",
                                                                   "why": PERSON_ONLY_REFUSAL})
        store = make_store(db)
        if getattr(store, "objects", True) is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail={"refused": "photos are not set up on this deployment (no object store)"})
        try:
            got = await pp.upload(store, _own(user), pp.decode_data(body.image))
        except pp.Refused as r:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"refused": str(r)})
        return {"subject": str(user.id), **got}

    @router.delete("/user/photo")
    async def delete_own_photo(request: Request, user: User = Depends(get_current_person),
                               db: AsyncSession = Depends(get_db)):
        """Remove THEIR OWN upload; their sign-in provider's photo or Gravatar shows again, else a
        monogram. The person only."""
        if _delegated(request):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"refused": "the photo is the person's",
                                                                   "why": PERSON_ONLY_REFUSAL})
        return {"subject": str(user.id), **(await pp.remove_upload(make_store(db), _own(user)))}

    async def _user(user_id: str, db: AsyncSession) -> User:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown user")
        user = (await db.execute(select(User).where(User.id == uid))).scalars().first()
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown user")
        return user

    @router.get("/internal/users/{user_id}/photo", include_in_schema=False)
    async def get_photo_internal(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        """The bytes of the photo shown for this person, or 404. For agent-api, which has already
        decided the viewer may see it."""
        check_internal_no_dev_bypass(request)
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no photo")
        store = make_store(db)
        got = pp.pick(await store.rows(uid))
        body = await store.read(uid, got[0], got[1]) if got else None
        if not got or not body:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no photo")
        source, row = got
        return Response(content=body, media_type=row["content_type"], headers={
            "ETag": f'"{row.get("sha256", "")[:32]}"', "X-Photo-Source": source,
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @router.put("/internal/users/{user_id}/signin-photo", include_in_schema=False)
    async def put_signin_photo(user_id: str, payload: SigninPhoto, request: Request,
                               db: AsyncSession = Depends(get_db)):
        """What THIS sign-in says about the person's photo. Called by the terminal after the account
        is found or created; a failure here never refuses a sign-in (the terminal does not wait)."""
        check_internal_no_dev_bypass(request)
        user = await _user(user_id, db)
        store = make_store(db)
        if getattr(store, "objects", True) is None:
            return {"provider": "skipped", "gravatar": "skipped", "why": "no object store"}
        provider = payload.provider if payload.provider in pp.PROVIDERS else ""
        data = user.data if isinstance(user.data, dict) else {}
        bound = data.get("provider_subjects") if isinstance(data.get("provider_subjects"), dict) else {}
        url = ""
        if provider in ("google", "oidc"):
            # The picture's address comes ONLY from this person's own verified ID token: signature,
            # issuer, audience and expiry checked by `idp_tokens`, and its subject must be the
            # provider identity this account is bound to. Anything else: nothing is fetched.
            try:
                claims = await idp_tokens.verified_claims(provider, payload.id_token)
            except idp_tokens.Invalid as e:
                logger.info("sign-in photo for user %s: %s token refused (%s)", user.id, provider, e)
                return {"provider": "refused", "gravatar": "skipped"}
            if not bound.get(provider) or idp_tokens.bound_subject(provider, claims) != bound.get(provider):
                logger.info("sign-in photo for user %s: the %s token is not this account's", user.id, provider)
                return {"provider": "refused", "gravatar": "skipped"}
            picture = claims.get("picture")
            url = picture.strip() if isinstance(picture, str) else ""
        try:
            return await pp.at_signin(store, int(user.id), email=user.email or "", provider=provider,
                                      url=url,
                                      access_token=payload.access_token if provider == "microsoft" else "",
                                      bound_subjects=bound, fetch=outbound, gravatar=gravatar_enabled())
        except Exception:  # noqa: BLE001 — a store hiccup never fails a sign-in
            logger.warning("sign-in photo for user %s not stored", user.id)
            return {"provider": "skipped", "gravatar": "skipped", "why": "store unavailable"}

    return router
