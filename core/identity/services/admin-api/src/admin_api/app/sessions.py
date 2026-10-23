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

Every session ends after `SESSION_LIFETIME_SECONDS`, the lifetime of the cookie that carries it.
No door trades an existing credential up to a session: a browser whose cookie still holds a
pre-session `terminal-login` KEY signs in again, and that sign-in mints its session. The schema
convergence gives every session and every sign-in key that has no end the one its cookie had
(`schema/sync.py`, MIGRATION-0011).
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema.models import APIToken, User
from ..schema.sync import LOGIN_LIFETIME_SECONDS
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
#: How long a session lives: the same 30 days as the browser cookie that carries it. A session that
#: outlives its cookie serves nobody at a keyboard, and one with no end at all is a standing key.
SESSION_LIFETIME_SECONDS = LOGIN_LIFETIME_SECONDS


class SessionMintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int = Field(..., ge=1)
    name: Optional[str] = Field(default=None, max_length=255)
    expires_in: Optional[int] = Field(default=None, gt=0)


def login_name_reserved() -> bool:
    """With sessions on, the sign-in never mints a key, so no key may carry its name."""
    return bool(os.environ.get(SESSION_SECRET_ENV, ""))


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
        lifetime = min(body.expires_in, SESSION_LIFETIME_SECONDS) if body.expires_in else SESSION_LIFETIME_SECONDS
        expires_at = datetime.utcnow() + timedelta(seconds=lifetime)
        tok = APIToken(token=generate_prefixed_token(SESSION_PREFIX_SCOPE), user_id=user.id,
                       scopes=list(SESSION_SCOPES), name=body.name or DEFAULT_NAME,
                       created_at=datetime.utcnow(), expires_at=expires_at, kind=KIND_SESSION)
        db.add(tok)
        await db.commit()
        await db.refresh(tok)
        return {"token": tok.token, "id": tok.id, "user_id": user.id, "scopes": list(SESSION_SCOPES),
                "credential_class": KIND_SESSION}

    return router
