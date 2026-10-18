"""sessions.py — the `session` credential class (PRD 0.13.4 D13; MIGRATION-0010).

A signed-in browser and a self-serve API key used to be the same row: the terminal's sign-in minted
an ordinary `bot,tx,browser` key through `/admin/users/{id}/tokens`, exactly what `/api/tokens`
hands anyone who asks, so "browser" proved nothing about who was at the keyboard. A decision that
must be a person's own act (a policy change, a routine's activation) had nothing to stand on.

`POST /internal/sessions` is the one door that mints `api_tokens.kind = 'session'`, and only the
terminal holds both of its secrets:

  * the internal tier (`X-Internal-Secret`, `check_internal_no_dev_bypass` — it mints a credential
    for an account named by the caller, so there is no dev-mode escape), and
  * `X-Terminal-Session-Secret`, compared in constant time with `VEXA_TERMINAL_SESSION_SECRET`.
    Unset → 503 (the door is closed on this deployment); wrong or missing → 403.

The internal secret alone is shared by every service inside the deployment; the second secret is
what keeps a compromised meeting-api or flows from minting a person's session. `/admin/users/{id}/
tokens` has no parameter that reaches `kind`, so it never produces one.

`/internal/validate` then answers `credential_class` for every bearer: `session` for this row,
`key` for every other API key, `delegation` for a worker's `vxd_` token.

`POST /internal/sessions/upgrade {token}` (same two secrets) moves a browser that signed in BEFORE
this door existed onto a session (hotfix 2026-10-18): its cookie still holds the old `terminal-login`
KEY, so every decision that needs a session (a card press, D13) was refused with no way out short of
signing in again. Only that exact legacy shape upgrades — a `key` row named `terminal-login` with
scopes `bot,tx,browser` and no expiry, which only the sign-in ever minted (the terminal's self-serve
`/api/tokens` refuses that name). A session, a delegation, an expiring key, any other key → 409. The
old key is retired in the same commit: it expires after a short grace (requests already in flight
with it still land) and, now expiring, can never be upgraded again — one key, at most one session.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema.models import APIToken, User
from ..token_scope import generate_prefixed_token
from .db import get_db
from .internal_tier import check_internal_no_dev_bypass

KIND_KEY = "key"
KIND_SESSION = "session"
#: What a session may do at the edge — the same reach the terminal's login key always had.
SESSION_SCOPES = ("bot", "tx", "browser")
#: The prefix a session token carries (`vxa_browser_…`): the existing `vxa_<scope>_` shape, so
#: nothing that pattern-matches token prefixes has to learn a new one.
SESSION_PREFIX_SCOPE = "browser"
SESSION_SECRET_ENV = "VEXA_TERMINAL_SESSION_SECRET"
SESSION_SECRET_HEADER = "X-Terminal-Session-Secret"
DEFAULT_NAME = "terminal-login"
#: How long a retired login key keeps working after its upgrade — requests the browser already sent
#: with it must still land; the cookie it receives carries the session from then on.
UPGRADE_GRACE_SECONDS = 600


class SessionMintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int = Field(..., ge=1)
    name: Optional[str] = Field(default=None, max_length=255)
    expires_in: Optional[int] = Field(default=None, gt=0)


class SessionUpgradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(..., min_length=1, max_length=255)


def is_legacy_login_key(api_token: APIToken) -> bool:
    """The exact row the sign-in minted before sessions existed — nothing else upgrades."""
    return (getattr(api_token, "kind", None) in (None, KIND_KEY)
            and api_token.name == DEFAULT_NAME
            and sorted(api_token.scopes or []) == sorted(SESSION_SCOPES)
            and api_token.expires_at is None)


def check_session_secret(request: Request) -> None:
    secret = os.environ.get(SESSION_SECRET_ENV, "")
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"{SESSION_SECRET_ENV} not configured — sessions are not minted here")
    provided = request.headers.get(SESSION_SECRET_HEADER, "")
    if not hmac.compare_digest(provided.encode(), secret.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid terminal session secret")


def credential_class_of(api_token: APIToken) -> str:
    """`session` for a row the session door minted; `key` for every other API key (legacy too)."""
    return KIND_SESSION if getattr(api_token, "kind", None) == KIND_SESSION else KIND_KEY


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/internal/sessions", include_in_schema=False, status_code=status.HTTP_201_CREATED)
    async def mint_session(body: SessionMintRequest, request: Request,
                           db: AsyncSession = Depends(get_db)):
        check_internal_no_dev_bypass(request)
        check_session_secret(request)
        user = await db.get(User, body.user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        from . import principals as principals_mod  # noqa: PLC0415
        if principals_mod.is_principal_account(user):
            # A workspace principal (D2) never signs in: nobody is at its keyboard.
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail="a workspace principal holds no session")
        expires_at = None
        if body.expires_in:
            expires_at = datetime.utcnow() + timedelta(seconds=body.expires_in)
        tok = APIToken(token=generate_prefixed_token(SESSION_PREFIX_SCOPE), user_id=user.id,
                       scopes=list(SESSION_SCOPES), name=body.name or DEFAULT_NAME,
                       created_at=datetime.utcnow(), expires_at=expires_at, kind=KIND_SESSION)
        db.add(tok)
        await db.commit()
        await db.refresh(tok)
        return {"token": tok.token, "id": tok.id, "user_id": user.id, "scopes": list(SESSION_SCOPES),
                "credential_class": KIND_SESSION}

    @router.post("/internal/sessions/upgrade", include_in_schema=False, status_code=status.HTTP_201_CREATED)
    async def upgrade_session(body: SessionUpgradeRequest, request: Request,
                              db: AsyncSession = Depends(get_db)):
        check_internal_no_dev_bypass(request)
        check_session_secret(request)
        row = (await db.execute(
            select(APIToken, User).join(User, APIToken.user_id == User.id).where(APIToken.token == body.token)
        )).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown credential")
        old, user = row[0], row[1]
        now = datetime.utcnow()
        if not is_legacy_login_key(old):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail={"refused": "not_a_login_key",
                                        "note": "only a browser's pre-session sign-in key is upgraded"})
        from . import principals as principals_mod  # noqa: PLC0415
        if principals_mod.is_principal_account(user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="a workspace principal holds no session")
        old.expires_at = now + timedelta(seconds=UPGRADE_GRACE_SECONDS)
        tok = APIToken(token=generate_prefixed_token(SESSION_PREFIX_SCOPE), user_id=user.id,
                       scopes=list(SESSION_SCOPES), name=DEFAULT_NAME,
                       created_at=now, kind=KIND_SESSION)
        db.add(tok)
        await db.commit()
        await db.refresh(tok)
        return {"token": tok.token, "id": tok.id, "user_id": user.id, "scopes": list(SESSION_SCOPES),
                "credential_class": KIND_SESSION, "retired": old.id}

    return router
