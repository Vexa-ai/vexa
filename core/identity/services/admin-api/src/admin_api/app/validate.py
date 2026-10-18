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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sqlalchemy.future import select

from .. import delegation as delegation_mod
from .. import obs
from ..schema.models import APIToken, User
from . import delegation_revocation as revocation
from . import admin_account
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
    """The dispatch's ceiling, copied from the delegation token's `scope` and `target`, and its `unit`."""
    regime: Literal["human", "autonomous"]        # as minted (delegation.regime_for_trigger)
    workspaces: Union[Literal["*"], List[str]]    # "*" (human regime) or the isolation set
    target: Optional[str] = None                  # the chat's default workspace; omitted when none
    unit: Optional[str] = None                    # the agent unit (session) it was minted for
    # a turn other people read (delegation.v1 scope.shared, ADR-0048); omitted on a private turn
    shared: Optional[Literal[True]] = None
    # some readers of that shared turn are outside the org (delegation.v1 scope.audience, rooms)
    audience: Optional[Literal["cross_org"]] = None
    # a routine's delegated authority (delegation.v1 scope.grants + scope.flow, flows phase 0):
    # the unattended verbs its person granted it, and the routine they belong to; omitted when none
    grants: Optional[List[str]] = None
    flow: Optional[str] = None
    # D2 (0.13.4): the token named a WORKSPACE principal (a room's turn); `user_id` is then that
    # principal's own id, `bills_to` the account it debits. Omitted for a person's worker.
    principal_kind: Optional[Literal["workspace"]] = None
    principal_workspace: Optional[str] = None
    on_behalf_of: Optional[str] = None
    bills_to: Optional[int] = None


class ValidatedIdentity(BaseModel):
    """Who the bearer is. The first five fields and `credential_class` are always present; the rest
    only when they apply."""
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
    # THE ADMIN ACCOUNT (app/admin_account.py): the deployment's dedicated account, the only one
    # that writes `_global`. Always stated for an API key — positive evidence, so a resolver that
    # finds it missing (an older identity) reads "no". False for a worker's delegation token: a
    # worker is never the account; `person_admin_account` says whether its person is.
    admin_account: Optional[bool] = None
    person_admin_account: Optional[bool] = None
    # THE CREDENTIAL CLASS (D13, MIGRATION-0010) — always stated, positive evidence: `session` for the
    # terminal's signed-in browser (minted only by `POST /internal/sessions`), `key` for every other
    # API key (legacy rows included), `delegation` for a worker's `vxd_` token. A decision that must
    # be a person's own act asks for `session`; a resolver that finds it missing reads "not one".
    credential_class: Literal["session", "key", "delegation"]


def identity_of(user: User, *, scopes: List[str], is_admin: bool) -> Dict[str, Any]:
    """The fields of a `ValidatedIdentity` for `user` — only the ones that apply."""
    data_blob = user.data if isinstance(user.data, dict) else {}
    is_account = admin_account.is_admin_account(user.email, data_blob)
    resp: Dict[str, Any] = {
        "user_id": user.id,
        "scopes": scopes,
        # The admin account launches no bot, whatever its row says: 0 is meeting-api's "no bots".
        "max_concurrent": admin_account.MAX_CONCURRENT_BOTS if is_account else user.max_concurrent_bots,
        "email": user.email,
        "is_admin": is_admin,
        "admin_account": is_account,
    }
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
    # WHO THE TOKEN ACTS AS (D2): a person, or a WORKSPACE principal (a room's turn). A workspace
    # principal's `sub` is its own numeric id — an account row that owns nothing of a person's — so
    # every service keyed on an account keeps working and none of them reaches a member's things.
    # The claims must agree with identity's table (kind, workspace, active, a payer that exists).
    principal = delegation_mod.principal_of(claims)
    is_ws = principal["kind"] == delegation_mod.PRINCIPAL_KIND_WORKSPACE
    ws_row = None
    if is_ws:
        from . import principals as principals_mod  # noqa: PLC0415
        ws_row = await principals_mod.lookup(db, uid)
        if (ws_row is None or ws_row.kind != principals_mod.KIND_WORKSPACE
                or ws_row.status != principals_mod.STATUS_ACTIVE
                or ws_row.workspace_id != principal["workspace"] or not ws_row.bills_to
                or not principal["on_behalf_of"] or not ceiling.get("shared")
                or not isinstance(ceiling.get("workspaces"), list) or "grants" in ceiling):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: principal")
    elif claims.get("principal_kind") is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: principal")
    ceiling_subject = str(uid)
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
            ceiling["workspaces"], w, subject=ceiling_subject)]
    # The WRITE set is narrowed by the ceiling's own rule (`ceiling_allows`), never by the read
    # result: reads also include what every subject reads (`_global`), and a worker must not be
    # able to put a meeting into `_global` because its person happens to edit it (R1801-7).
    if "writable_workspaces" in resp:
        resp["writable_workspaces"] = [w for w in resp["writable_workspaces"] if delegation_mod.ceiling_allows(
            ceiling["workspaces"], w, subject=ceiling_subject)]
    if is_ws:
        # A workspace principal's account holds no membership rows: it reads its own workspace and
        # what its ceiling mounts read-only, and writes its workspace only if the ceiling does.
        listed = [str(w) for w in ceiling["workspaces"]]
        reads = [w[len(delegation_mod.READ_ONLY_PREFIX):] if w.startswith(delegation_mod.READ_ONLY_PREFIX) else w
                 for w in listed]
        resp["workspaces"] = sorted(set(reads))
        writable = [w for w in listed if not w.startswith(delegation_mod.READ_ONLY_PREFIX)]
        if writable:
            resp["writable_workspaces"] = sorted(set(writable))
        else:
            resp.pop("writable_workspaces", None)
    resp["delegation"] = ceiling
    if is_ws:
        resp["delegation"]["principal_kind"] = delegation_mod.PRINCIPAL_KIND_WORKSPACE
        resp["delegation"]["principal_workspace"] = ws_row.workspace_id
        resp["delegation"]["on_behalf_of"] = principal["on_behalf_of"]
        resp["delegation"]["bills_to"] = int(ws_row.bills_to)
        # A ROOM'S BOTS ARE ITS PAYER'S (int-s1): meeting-api keys a principal's bot to `bills_to`,
        # so the concurrency cap it pre-checks is the payer's, never the principal account's 0.
        payer = (await db.execute(select(User).where(User.id == int(ws_row.bills_to)))).scalar_one_or_none()
        resp["max_concurrent"] = int(payer.max_concurrent_bots) if payer is not None else 0
    if claims.get("target"):
        resp["delegation"]["target"] = str(claims["target"])
    # THE SESSION, from inside the signature (Vexa-ai/vexa#1783): the unit agent-api minted the token
    # for. Passed on so the gateway signs it into the identity it forwards; never a grant.
    if isinstance(claims.get("unit"), str) and claims["unit"].strip():
        resp["delegation"]["unit"] = claims["unit"].strip()
        obs.set_session(resp["delegation"]["unit"])
    obs.log_event("delegation_validated", audience="system", user_id=str(uid),
                  fields={"regime": ceiling["regime"], **({"principal_kind": "workspace"} if is_ws else {})})
    # A WORKSPACE IS NEVER THE ADMIN (D2): the payer's admin bit is not the room's to borrow.
    resp["person_is_admin"] = False if is_ws else signin_allow.is_admin(
        user.email, user.data, signin_allow.admin_emails()[0])
    resp["person_admin_account"] = False if is_ws else bool(resp["admin_account"])
    resp["admin_account"] = False
    resp["credential_class"] = "delegation"
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
    out: Dict[str, Any] = {"regime": regime, "workspaces": workspaces}
    if delegation_mod.is_shared(scope):
        out["shared"] = True    # carried on, so every service can refuse a person-wide read
    # WHO READS IT, BEYOND "OTHER PEOPLE" (rooms): carried on only beside `shared`, so crm-api can
    # answer a cross-org room with public fields only. A value that is not the one word is refused.
    if "audience" in scope:
        if not delegation_mod.is_cross_org(scope) or not out.get("shared"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: scope")
        out["audience"] = delegation_mod.AUDIENCE_CROSS_ORG
    # A ROUTINE'S GRANTS, carried on only when well-formed: a list inside the closed vocabulary,
    # beside the routine that holds them, on an autonomous turn. Anything else names no grant a
    # service could honour, so the token is refused rather than read generously.
    grants, flow = scope.get("grants"), scope.get("flow")
    if grants is not None or flow is not None:
        try:
            clean = delegation_mod.clean_grants(grants if isinstance(grants, list) else None)
        except ValueError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: scope")
        if (not clean or not isinstance(grants, list) or clean != grants or regime != "autonomous"
                or not isinstance(flow, str) or not flow.strip()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: scope")
        out["grants"] = clean
        out["flow"] = flow.strip()
    return out


#: NOTHING A PERSON DOES WAITS ON THIS ROUTE'S WRITES (2026-10-13 11:25 UTC: every container healthy,
#: validate at 23–38 s, Postgres sessions waiting on a lock and on IO under a loaded host, and the
#: gateway answering `503 Authentication temporarily unavailable` mid-chat). It stamped
#: `last_used_at` and committed on every call, so concurrent calls with one key queued on the row
#: lock and the synchronous commit. The read is bounded too, so a stuck database fails in seconds
#: (the gateway then answers its typed 503 at once) rather than holding the request.
LAST_USED_RESOLUTION = timedelta(seconds=60)
READ_BOUNDS = ("SET LOCAL statement_timeout = '3s'", "SET LOCAL lock_timeout = '1s'")
STAMP_BOUNDS = ("SET LOCAL lock_timeout = '100ms'", "SET LOCAL synchronous_commit = off")
STAMP = text("UPDATE api_tokens SET last_used_at = :now WHERE id = "
             "(SELECT id FROM api_tokens WHERE id = :id FOR UPDATE SKIP LOCKED)")


def _postgres(db) -> bool:
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:  # noqa: BLE001 — a session with no bind (a test fake) is not Postgres
        return False


async def _bound_reads(db) -> None:
    if _postgres(db):
        for stmt in READ_BOUNDS:
            await db.execute(text(stmt))


async def _stamp_last_used(db, api_token) -> None:
    """Stamp the key's last use — at most once per LAST_USED_RESOLUTION, skipping a row another
    transaction has locked, not waiting for the commit to reach disk, and never failing the answer."""
    now = datetime.utcnow()
    if api_token.last_used_at is not None and now - api_token.last_used_at < LAST_USED_RESOLUTION:
        return
    try:
        if _postgres(db):
            for stmt in STAMP_BOUNDS:
                await db.execute(text(stmt))
            await db.execute(STAMP, {"now": now, "id": api_token.id})
        else:
            api_token.last_used_at = now
        await db.commit()
    except Exception as e:  # noqa: BLE001 — the stamp is advisory; the answer is not
        await db.rollback()
        obs.log_event("last_used_not_stamped", audience="system", level="warning",
                      fields={"reason": type(e).__name__})


router = APIRouter()


@router.post("/internal/validate", include_in_schema=False, response_model=ValidatedIdentity,
             response_model_exclude_unset=True)
async def validate_token(payload: ValidateRequest, request: Request,
                         db: AsyncSession = Depends(get_db)):
    check_internal(request)  # fail closed: no secret configured → 503 unless dev mode
    token = payload.token or ""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    await _bound_reads(db)
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
    # A WORKSPACE PRINCIPAL's account (D2) is never a key holder: nothing mints one, and a row that
    # somehow carries one is refused rather than answered as a person.
    from . import principals as principals_mod  # noqa: PLC0415
    if principals_mod.is_principal_account(user):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_TOKEN)

    if api_token.expires_at is not None and api_token.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    await _stamp_last_used(db, api_token)

    scopes = list(api_token.scopes) if api_token.scopes else ["legacy"]
    is_admin = signin_allow.is_admin(user.email, user.data, signin_allow.admin_emails()[0])
    from .sessions import credential_class_of  # noqa: PLC0415
    resp = identity_of(user, scopes=scopes, is_admin=is_admin)
    resp["credential_class"] = credential_class_of(api_token)
    return ValidatedIdentity.model_validate(resp)
