"""The gateway's authz oracle — `POST /internal/validate`: a bearer in, the identity it names out.

Every resolver asks here (the gateway, flows, the terminal's server), so none of them holds a token
table or the delegation key. Two kinds of bearer answer the same shape, `ValidatedIdentity`:

  * an API key (`vxa_…`): the account behind it, its scopes (`["legacy"]` for an unscoped row) and
    THE admin test (`signin_allow.is_admin`). An expired key is 401; every hit bumps `last_used_at`.
  * a worker's delegation token (`vxd_…`), minted per dispatch by agent-api (`core/agent/shared/
    delegation.py`, vendored here byte for byte) and verified with `VEXA_MCP_DELEGATION_SECRET`. It
    says WHO the worker acts for and the ceiling the dispatch was granted. The account must still
    exist: a token minted for a deleted user names nobody. Scopes are the two service domains a
    person's agent acts in (`bot`, `tx`) — never `browser`, never admin — and the ceiling travels on
    as `delegation`, for the services to enforce.

Internal tier, failing closed (`internal_tier.check_internal`). The request and the response are
named models, so this wire is declared here once. A field that does not apply to a bearer is OMITTED,
never sent as null (`response_model_exclude_unset`) — the shape every caller has always read.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .. import delegation as delegation_mod
from ..schema.models import APIToken, User
from . import signin_allow
from .db import get_db
from .internal_tier import check_internal

#: What a worker's delegation token may do at the edge: act in the two service domains for the
#: person it names — the same reach a person's own bot+tx key has, and nothing operator-shaped.
DELEGATED_SCOPES = ("bot", "tx")


class ValidateRequest(BaseModel):
    """The bearer to resolve. A missing or empty `token` is a 401 ("Missing token"), not a 422: it is
    an answer about the bearer, which is what every caller branches on."""
    model_config = ConfigDict(extra="forbid")
    token: Optional[str] = None


class DelegationCeiling(BaseModel):
    """The dispatch's ceiling, copied from the delegation token's `scope` and `target`."""
    regime: Literal["human", "autonomous"]        # as minted (delegation.regime_for_trigger)
    workspaces: Union[Literal["*"], List[str]]    # "*" (human regime) or the isolation set
    target: Optional[str] = None                  # the chat's default workspace; omitted when none


class ValidatedIdentity(BaseModel):
    """Who the bearer is. The first five fields are always present; the rest only when they apply."""
    user_id: int
    scopes: List[str]
    max_concurrent: int
    email: str
    # THE admin test (signin_allow.is_admin) for an API key; always False for a worker's delegation.
    # The terminal's admin gate reads this and holds no list of its own.
    is_admin: bool
    webhook_url: Optional[str] = None             # the account's webhook, when one is set
    webhook_secret: Optional[str] = None          # only beside a webhook_url
    webhook_events: Optional[Dict[str, Any]] = None
    # The caller's shared-workspace membership ids (from the derived users.data.memberships[]), so
    # the gateway can inject x-user-workspaces → meeting-api authorizes a member's transcript
    # subscribe. Present when the account carries a membership list.
    workspaces: Optional[List[str]] = None
    delegation: Optional[DelegationCeiling] = None   # delegation tokens only
    # WHO THE WORKER ACTS FOR, as a fact about that person — never a role the worker holds
    # (`is_admin` stays False). The MCP edge spends the deployment's operator key for a worker only
    # when its person is the instance admin AND the person is in the loop (regime `human`); it asks
    # the gateway, which reads this beside the regime. Delegation tokens only.
    person_is_admin: Optional[bool] = None


def identity_of(user: User, *, scopes: List[str], is_admin: bool) -> Dict[str, Any]:
    """The fields of a `ValidatedIdentity` for `user` — only the ones that apply."""
    resp: Dict[str, Any] = {
        "user_id": user.id,
        "scopes": scopes,
        "max_concurrent": user.max_concurrent_bots,
        "email": user.email,
        "is_admin": is_admin,
    }
    data_blob = user.data if isinstance(user.data, dict) else {}
    if data_blob.get("webhook_url"):
        resp["webhook_url"] = data_blob["webhook_url"]
        if data_blob.get("webhook_secret"):
            resp["webhook_secret"] = data_blob["webhook_secret"]
        if data_blob.get("webhook_events"):
            resp["webhook_events"] = data_blob["webhook_events"]
    memberships = data_blob.get("memberships")
    if isinstance(memberships, list):
        resp["workspaces"] = [str(m["workspace_id"]) for m in memberships
                              if isinstance(m, dict) and m.get("workspace_id")]
    return resp


async def _validate_delegation(token: str, db: AsyncSession) -> Dict[str, Any]:
    secret = os.environ.get("VEXA_MCP_DELEGATION_SECRET", "")
    if not secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="Delegation tokens are not accepted on this deployment")
    try:
        claims = delegation_mod.verify_delegation(secret, token)
    except delegation_mod.DelegationError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Invalid delegation: {e.reason}")
    try:
        uid = int(str(claims["sub"]))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: malformed")
    ceiling = _ceiling(claims.get("scope"))
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: no such user")
    resp = identity_of(user, scopes=list(DELEGATED_SCOPES), is_admin=False)
    resp["delegation"] = ceiling
    if claims.get("target"):
        resp["delegation"]["target"] = str(claims["target"])
    resp["person_is_admin"] = signin_allow.is_admin(
        user.email, user.data, signin_allow.admin_emails()[0])
    return resp


def _ceiling(scope: Any) -> Dict[str, Any]:
    """A signed token's `scope` as the sealed ceiling (identity.v1 ValidateDelegation), or a 401: a
    regime outside `human`/`autonomous`, or workspaces that are neither `"*"` nor a list of slugs,
    describe no ceiling the services behind the gateway could enforce."""
    scope = scope if isinstance(scope, dict) else {}
    regime, workspaces = scope.get("regime"), scope.get("workspaces")
    if regime not in ("human", "autonomous") or not (
            workspaces == "*" or (isinstance(workspaces, list)
                                  and all(isinstance(w, str) for w in workspaces))):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: scope")
    return {"regime": regime, "workspaces": workspaces}


router = APIRouter()


@router.post("/internal/validate", include_in_schema=False, response_model=ValidatedIdentity,
             response_model_exclude_unset=True)
async def validate_token(payload: ValidateRequest, request: Request,
                         db: AsyncSession = Depends(get_db)):
    check_internal(request)  # fail closed: no secret configured → 503 unless dev mode
    token = payload.token or ""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    if delegation_mod.is_delegation_token(token):
        return ValidatedIdentity.model_validate(await _validate_delegation(token, db))

    row = (await db.execute(
        select(APIToken, User).join(User, APIToken.user_id == User.id)
        .where(APIToken.token == token)
    )).first()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    api_token, user = row

    if api_token.expires_at is not None and api_token.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    api_token.last_used_at = datetime.utcnow()
    await db.commit()

    scopes = list(api_token.scopes) if api_token.scopes else ["legacy"]
    is_admin = signin_allow.is_admin(user.email, user.data, signin_allow.admin_emails()[0])
    return ValidatedIdentity.model_validate(identity_of(user, scopes=scopes, is_admin=is_admin))
