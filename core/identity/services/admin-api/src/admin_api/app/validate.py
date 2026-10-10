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

A DELEGATION ANSWER GOES ONLY TO A CALLER THAT ASKED FOR ONE. A worker's 200 looks like a person's
200 plus two fields, so a resolver that predates delegation reads it as the person and drops the
ceiling. A caller therefore declares that it reads `delegation`, with `ACCEPTS_DELEGATION_HEADER`
set to `1` (identity.v1 `AcceptsDelegationHeader`); to any other caller a `vxd_` bearer is refused
exactly like a bearer nobody answers to (401 `Invalid token`). An old resolver in front of this
identity fails closed instead of forwarding the worker as its person.

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
from . import delegation_revocation as revocation
from . import signin_allow
from .db import get_db
from .internal_tier import check_internal

#: What a worker's delegation token may do at the edge: act in the two service domains for the
#: person it names — the same reach a person's own bot+tx key has, and nothing operator-shaped.
DELEGATED_SCOPES = ("bot", "tx")

#: The request header a resolver sets, to `ACCEPTS_DELEGATION_VALUE`, to say it reads `delegation`
#: (identity.v1 `AcceptsDelegationHeader`). In the `x-vexa-internal-` family, which the gateway never
#: forwards from a client.
ACCEPTS_DELEGATION_HEADER = "x-vexa-internal-accepts-delegation"
ACCEPTS_DELEGATION_VALUE = "1"

#: The refusal for a bearer nobody answers to — and for a delegation token presented by a caller that
#: did not declare it reads one. One string, so the two cannot be told apart.
INVALID_TOKEN = "Invalid token"

#: The refusal for a delegation token when the revocation store cannot be read (503): whether the
#: token's unit ended cannot be known, so the token is not answered for. API keys are unaffected.
DELEGATION_REVOCATION_UNAVAILABLE = "Delegation revocation store unavailable"


def accepts_delegation(request: Request) -> bool:
    """Did the caller declare that it reads a delegation answer? Exactly `1`; anything else is no."""
    return (request.headers.get(ACCEPTS_DELEGATION_HEADER) or "").strip() == ACCEPTS_DELEGATION_VALUE


#: The membership roles that may write into a shared workspace (agent-api's role ladder:
#: viewer < contributor < owner; "reader" is the person-facing word for viewer).
WRITE_ROLES = frozenset({"contributor", "owner"})


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
    # subscribe. Present when the account carries a membership list. For a delegation token, only
    # the memberships inside the dispatch's ceiling (`_validate_delegation`).
    workspaces: Optional[List[str]] = None
    # The subset the caller may WRITE into (contributor or owner), so meeting-api can refuse to bind
    # a meeting into a workspace its owner only reads. Bounded by the same ceiling for a delegation.
    writable_workspaces: Optional[List[str]] = None
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
        writable = [str(m["workspace_id"]) for m in memberships
                    if isinstance(m, dict) and m.get("workspace_id")
                    and str(m.get("role") or "").lower() in WRITE_ROLES]
        if writable:   # present only when there is one, like the webhook fields
            resp["writable_workspaces"] = writable
    return resp


async def _validate_delegation(token: str, db: AsyncSession) -> Dict[str, Any]:
    secret = os.environ.get("VEXA_MCP_DELEGATION_SECRET", "")
    if not secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="Delegation tokens are not accepted on this deployment")
    try:
        claims = delegation_mod.verify_delegation(secret, token)
        # Revoked when the unit it was minted for ended; asked only once the signature verified.
        jti = claims.get("jti")
        if not isinstance(jti, str) or not jti:
            raise delegation_mod.Malformed("delegation token names no jti")
        # Admitted only while agent-api's live record exists and no revocation does: an evicted or
        # never-written record refuses the token (delegation_revocation.is_admitted).
        if not await revocation.is_admitted(jti):
            raise delegation_mod.Revoked("delegation token has been revoked")
    except delegation_mod.DelegationError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Invalid delegation: {e.reason}")
    except revocation.Unavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=DELEGATION_REVOCATION_UNAVAILABLE)
    try:
        uid = int(str(claims["sub"]))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: malformed")
    ceiling = _ceiling(claims.get("scope"))
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: no such user")
    resp = identity_of(user, scopes=list(DELEGATED_SCOPES), is_admin=False)
    # THE WORKER'S MEMBERSHIPS ARE THE PERSON'S, NARROWED TO ITS CEILING. Services behind the gateway
    # read `workspaces` as the shared workspaces this bearer reads through — meeting-api grants a read
    # of a meeting bound to any of them — so a worker granted workspace A is answered A, never the
    # person's B. The rule is the delegation contract's (`ceiling_reads`), the one agent-api's
    # resolvers apply; `"*"` (a person in the loop) keeps every membership.
    if "workspaces" in resp:
        resp["workspaces"] = [w for w in resp["workspaces"] if delegation_mod.ceiling_reads(
            ceiling["workspaces"], w, subject=str(uid))]
    # The WRITE set is narrowed by the ceiling's own rule (`ceiling_allows`), never by the read
    # result: reads also include what every subject reads (`_global`), and a worker must not be
    # able to put a meeting into `_global` because its person happens to edit it (R1801-7).
    if "writable_workspaces" in resp:
        resp["writable_workspaces"] = [w for w in resp["writable_workspaces"] if delegation_mod.ceiling_allows(
            ceiling["workspaces"], w, subject=str(uid))]
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
        if not accepts_delegation(request):
            # A caller that did not declare it reads `delegation` would take the worker for its
            # person: to that caller this bearer names nobody.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_TOKEN)
        return ValidatedIdentity.model_validate(await _validate_delegation(token, db))

    row = (await db.execute(
        select(APIToken, User).join(User, APIToken.user_id == User.id)
        .where(APIToken.token == token)
    )).first()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_TOKEN)
    api_token, user = row

    if api_token.expires_at is not None and api_token.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    api_token.last_used_at = datetime.utcnow()
    await db.commit()

    scopes = list(api_token.scopes) if api_token.scopes else ["legacy"]
    is_admin = signin_allow.is_admin(user.email, user.data, signin_allow.admin_emails()[0])
    return ValidatedIdentity.model_validate(identity_of(user, scopes=scopes, is_admin=is_admin))
