"""connect.py — a verified sign-in elsewhere gets its Vexa user and that user's own key, once.

    POST /connect   body ConnectRequest {provider, id_token}
                    answer ConnectResponse {user_id, key, key_expires_at, bound}

WHO CALLS IT. The credential broker, as the last step of a person's sign-in to another Vexa product
(the brain's Google sign-in): the broker got the ID token from Google itself, for that person, in
their browser. The gateway serves this one route with no key (`gateway/app.py`, `_forward_no_key`):
the raw ID token is the ONLY credential, and it is verified here. No admin key, no internal secret,
and an email is never taken as a parameter — the address is read only from the verified token.

THE STEPS, in order; nothing is written before step 4:

  1. the token is verified here (`idp_tokens.verified_claims(..., connect=True)`): the issuer's keys,
     the issuer, the expiry, and an audience that is one of the Google clients the operator listed in
     `VEXA_CONNECT_GOOGLE_CLIENT_IDS`. Unset, nothing verifies. `email_verified` must be true. Else 401.
     The sign-in must be fresh (issued within `idp_tokens.CONNECT_MAX_AGE_S`), and when the token
     names `azp` it must be a listed client too; a list with any implausible client id verifies nothing.
  2. each ID token works ONCE (a hash of it is kept in the service Redis until it expires; a replay is
     401), then a cap for each token subject (fails closed: 503).
  3. the admission rule every sign-in door asks (`signin_allow.decide`, through main.py's
     `_admission`). Refused: 403, and no user is made.
  4. NO ADMIN. An address `VEXA_ADMIN_EMAILS` or `VEXA_ADMIN_ACCOUNT` names is refused (403) before
     anything is created; then find the user by address (case folded, oldest first) or create one —
     main.py's `create_user`, the one point a person enters, where two creates at once end as one
     user on `users.email` — and a user who is an admin (`users.data.is_admin`) or carries the admin
     account stamp is refused (403) under the row's lock, before anything about them changes. The
     same three sources `/internal/validate` decides admin from, decided per user.
  5. bind the provider subject (`provider_subjects.bind`), under the user row's lock. Bound to another
     subject: 409, and nothing changes.
  6. end this user's earlier key named KEY_NAME and issue a new one, in the same transaction and
     under the same lock, so two connects at once leave ONE live key. It is answered this once.
  7. one audit line: the user id, the provider, first or same. Never the token, never the key.

THE KEY is issued as every key is today (`POST /admin/users/{id}/tokens`): one `api_tokens` row,
kind `key`, its value stored as the value, with an end time. It lives KEY_SECONDS — as long as a
Grant may live in broker.v1 — and a new connect ends the earlier one.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from .. import obs
from ..schema.models import APIToken, User
from . import admin_account
from . import idp_tokens
from . import provider_subjects
from . import signin_allow
from . import signin_links
from .db import get_db

#: One live key of this name for each user: a new connect ends the earlier one.
KEY_NAME = "brain-connect"
#: The scopes a person's key carries today; no scope logic of its own.
KEY_SCOPES = ["bot", "tx"]
#: As long as a Grant may live (broker.v1). Nobody renews it without the person's browser.
KEY_SECONDS = 365 * 24 * 3600

#: The cap for each token subject: RATE_LIMIT connects in a fixed window of RATE_WINDOW_SEC.
RATE_LIMIT = 10
RATE_WINDOW_SEC = 600
RATE_PREFIX = "vexa:rate:connect:"
#: A spent ID token: ``USED_PREFIX + sha256(token)``, kept until the token would have expired anyway.
USED_PREFIX = "vexa:connect:used:"

#: Why an admin is refused: a key made from a sign-in elsewhere never carries the admin's reach.
ADMIN_REFUSED = "an administrator cannot connect"

#: The longest name kept from the token (`users.name` is 100 characters).
NAME_MAX = 100


class ConnectRequest(BaseModel):
    """signin.v1 ``ConnectRequest``."""
    model_config = {"extra": "forbid"}

    provider: Literal["google"]
    id_token: str = Field(min_length=1, max_length=16384)


class ConnectResponse(BaseModel):
    """signin.v1 ``ConnectResponse``."""
    user_id: int = Field(ge=1)
    key: str
    key_expires_at: str
    bound: Literal["first", "same"]


def _email_verified(claims: dict) -> bool:
    """Google writes ``email_verified`` as a JSON true (older tokens as the string "true")."""
    v = claims.get("email_verified")
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


async def _take_rate(subject: str) -> bool:
    """One connect for ``subject`` in this window; False when over the cap. Raises on a store fault."""
    window = int(time.time()) // RATE_WINDOW_SEC
    key = f"{RATE_PREFIX}{subject}:{window}"
    r = signin_links._redis()
    count = int(await r.incr(key))
    if count == 1:
        await r.expire(key, RATE_WINDOW_SEC + 1)
    return count <= RATE_LIMIT


async def _spend(id_token: str, claims: dict) -> bool:
    """Record this ID token as used; False when it already was. Raises on a store fault. Only a hash
    of the token is kept, never the token."""
    try:
        exp = float(claims.get("exp"))
    except (TypeError, ValueError):
        exp = time.time()
    ttl = max(1, int(exp - time.time()) + idp_tokens.LEEWAY_S)
    digest = hashlib.sha256(id_token.strip().encode()).hexdigest()
    return bool(await signin_links._redis().set(USED_PREFIX + digest, "1", nx=True, ex=ttl))


FindOrCreate = Callable[[str, Optional[str], AsyncSession], Awaitable[int]]
Admission = Callable[[str, AsyncSession], Awaitable[Tuple[bool, str]]]
LoadUser = Callable[..., Awaitable[User]]
MintKey = Callable[..., APIToken]


def build_router(*, find_or_create: FindOrCreate, admission: Admission, load_user: LoadUser,
                 mint_key: MintKey) -> APIRouter:
    """The route, over main.py's own find-or-create, admission, user load and key minting (no second
    copy of any)."""
    router = APIRouter()

    @router.post("/connect", response_model=ConnectResponse, include_in_schema=False)
    async def connect(payload: ConnectRequest, db: AsyncSession = Depends(get_db)) -> Any:
        provider = payload.provider
        # 1 · the token, verified here. Nothing is read from it before it verifies.
        try:
            claims = await idp_tokens.verified_claims(provider, payload.id_token, connect=True)
        except idp_tokens.Invalid as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"the sign-in did not verify: {e}")
        email = signin_allow.normalize_email(claims.get("email"))
        if not _email_verified(claims) or not signin_allow.is_address(email):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                detail="the sign-in did not verify: its email is not verified")
        subject = idp_tokens.bound_subject(provider, claims)

        # 2 · each ID token works once (a replay is refused), then a cap for each token subject. A
        #     store that cannot answer refuses (fails closed).
        try:
            first_use = await _spend(payload.id_token, claims)
        except Exception as e:  # noqa: BLE001 — every store fault means "cannot record", which refuses
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail=f"connect store unavailable ({type(e).__name__})") from e
        if not first_use:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="the sign-in did not verify: it was already used")
        try:
            allowed = await _take_rate(subject)
        except Exception as e:  # noqa: BLE001 — every store fault means "cannot count", which refuses
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail=f"connect rate store unavailable ({type(e).__name__})") from e
        if not allowed:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="too many connects for this sign-in")

        # 3 · the admission rule every sign-in door asks. Refused: no user is made.
        admitted, why = await admission(email, db)
        if not admitted:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=why)

        # 4 · no admin: an address the deployment names as an admin or as the admin account is refused
        #     before anything is created (create_user would stamp a new admin account).
        admins = signin_allow.admin_emails()[0]
        if email in set(admins) or email in set(admin_account.designated()[0]):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=ADMIN_REFUSED)
        raw_name = claims.get("name")
        name = (raw_name.strip()[:NAME_MAX] or None) if isinstance(raw_name, str) else None
        user_id = await find_or_create(email, name, db)

        # 5 · bind the provider subject, under the user row's lock (held through step 6).
        user = await load_user(str(user_id), db, for_update=True)
        data = user.data if isinstance(user.data, dict) else {}
        if signin_allow.is_admin(user.email, data, admins) or admin_account.is_stamped(data):
            await db.rollback()
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=ADMIN_REFUSED)
        try:
            new_data, bound = provider_subjects.bind(data, subject)
        except provider_subjects.Mismatch as m:
            await db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail=f"this account is bound to another {m.args[0]} identity")
        except ValueError:
            await db.rollback()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="the sign-in did not verify: no subject")
        if bound == "first":
            from sqlalchemy.orm import attributes  # noqa: PLC0415

            user.data = new_data
            attributes.flag_modified(user, "data")
            db.add(user)

        # 6 · end the earlier key of this name, issue the new one; same transaction, same lock.
        await db.execute(delete(APIToken).where(APIToken.user_id == user_id, APIToken.name == KEY_NAME))
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=KEY_SECONDS)
        tok = mint_key(db, user_id=user_id, scopes=list(KEY_SCOPES), name=KEY_NAME, created_at=now,
                       expires_at=expires_at)
        await db.commit()

        # 7 · one audit line. Never the token, never the key.
        obs.log_event("connect_issued", audience="system", span="connect", user_id=str(user_id),
                      fields={"provider": provider, "bound": bound})
        return {"user_id": user_id, "key": tok.token,
                "key_expires_at": expires_at.replace(microsecond=0).isoformat() + "Z", "bound": bound}

    return router
