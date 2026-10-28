"""Transit keys — pass-through access that identity issues and identity answers for (ADR-0051).

A service that acts on a person's or customer's key (transcription-gateway today) does not read
`api_tokens`, does not trust a key's prefix, and does not mint keys itself. It is a registered
**target**; identity alone mints keys for it and tells it, per request, whom a key belongs to.

  Registry (operator, admin tier)
    PUT  /admin/transit-targets/{name}          register or update: actions, legacy prefixes
    GET  /admin/transit-targets                 list (never the secret)
    POST /admin/transit-targets/{name}/secret   issue the target's credential, answered ONCE

  Keys
    POST   /admin/users/{id}/transit-keys       an operator mints one for a person
    POST   /transit/keys                        the target asks identity to mint one on itself
    GET    /transit/keys?user_id=               the target lists a person's transit keys on itself
    DELETE /transit/keys?user_id=[&key_id=]     the target revokes them (never any other key)

  Validation (the target)
    POST /transit/validate {token, action}      -> {answer, signature}

A transit key is a person's key (`kind = 'key'`): hashed at rest (MIGRATION-0013), shown in their
list with its target and actions, revoked like any other. It reaches no gateway route (the edge's
`/internal/validate` refuses it). The target authenticates with `X-Transit-Target` and
`X-Transit-Target-Secret`; the answer is `answer` = base64url(JSON claims) and `signature` =
hex HMAC-SHA256(target secret, "transit.v1." + answer). Claims: `aud`, `sub` (user id), `email`,
`actions`, `key_id`, `token_sha256` (of the key asked about), `nonce`, `iat`, `exp` (iat + 60 s). The target checks the signature, `aud`, `exp` and
that the action it is about to perform is in `actions`.

Keys minted before transit keys existed keep working under the rule their target was registered
with: a key with no audience whose `vxa_<scope>_` prefix is one of the target's `legacy_prefixes`
(`""` = a key with no prefix) reads as a transit key on it with all of its actions. MIGRATION-0016
registers `transcription` with the prefixes transcription-gateway honoured (`tx`, `user`, `admin`,
none), so every existing transcription key keeps working. That rule only VALIDATES: a legacy key
stays the person's (or an operator's) to list and revoke, never the target's.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import string
import time
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import at_rest
from ..key_store import KIND_KEY, hash_token, stored_fields, token_matches
from ..schema.models import APIToken, TransitTarget, User
from ..token_scope import parse_token_scope
from .db import get_db

TARGET_HEADER = "X-Transit-Target"
TARGET_SECRET_HEADER = "X-Transit-Target-Secret"
SECRET_PURPOSE = "transit_target_secret"
ANSWER_TTL_SECONDS = 60
SIGNATURE_CONTEXT = b"transit.v1."
_NAME = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_PREFIX = re.compile(r"^[a-z]{0,16}$")


def generate_transit_token(audience: str, length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return f"vxt_{audience}_" + "".join(secrets.choice(alphabet) for _ in range(length))


def sign_answer(secret: str, claims: dict) -> dict:
    answer = base64.urlsafe_b64encode(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), SIGNATURE_CONTEXT + answer.encode(), hashlib.sha256).hexdigest()
    return {"answer": answer, "signature": sig}


def verify_answer(secret: str, signed: dict, *, audience: str, action: str, now: Optional[float] = None,
                  token: Optional[str] = None) -> Optional[dict]:
    """What a target runs on identity's answer (reference implementation; transcription-gateway
    carries a copy). The claims when the signature, audience, expiry and action all hold, and, given
    the key the target presented, when the answer is about that key."""
    answer, sig = str(signed.get("answer") or ""), str(signed.get("signature") or "")
    want = hmac.new(secret.encode(), SIGNATURE_CONTEXT + answer.encode(), hashlib.sha256).hexdigest()
    if not answer or not hmac.compare_digest(sig, want):
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(answer + "=" * (-len(answer) % 4)))
    except ValueError:
        return None
    if claims.get("aud") != audience or float(claims.get("exp", 0)) < (now or time.time()):
        return None
    if token is not None and claims.get("token_sha256") != hashlib.sha256(token.encode()).hexdigest():
        return None
    return claims if action in (claims.get("actions") or []) else None


# --- schemas ---------------------------------------------------------------------------------

class TargetUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: List[str] = Field(..., min_length=1)
    legacy_prefixes: List[str] = Field(default_factory=list)
    description: str = ""


class TransitKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audience: str
    actions: Optional[List[str]] = None
    name: Optional[str] = Field(default=None, max_length=255)
    expires_in: Optional[int] = Field(default=None, gt=0)


class TargetKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int = Field(..., ge=1)
    actions: Optional[List[str]] = None
    name: Optional[str] = Field(default=None, max_length=255)
    expires_in: Optional[int] = Field(default=None, gt=0)


class TransitValidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)


def _target_view(t: TransitTarget) -> dict:
    return {"name": t.name, "actions": list(t.actions or []), "legacy_prefixes": list(t.legacy_prefixes or []),
            "description": t.description or "", "secret_set": bool(t.secret),
            "secret_rotated_at": t.secret_rotated_at}


def _key_view(t: APIToken) -> dict:
    return {"id": t.id, "user_id": t.user_id, "name": t.name, "audience": t.audience,
            "actions": list(t.actions or []), "token_hint": t.token_hint, "created_at": t.created_at,
            "last_used_at": t.last_used_at, "expires_at": t.expires_at}


async def _target(db: AsyncSession, name: str) -> TransitTarget:
    t = await db.get(TransitTarget, name)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no transit target {name!r}")
    return t


async def authenticated_target(request: Request, db: AsyncSession = Depends(get_db)) -> TransitTarget:
    """The calling target: its name and its credential, compared in constant time. Any failure is
    the same 401, so a caller learns nothing about which targets exist."""
    name = (request.headers.get(TARGET_HEADER) or "").strip()
    given = request.headers.get(TARGET_SECRET_HEADER) or ""
    t = await db.get(TransitTarget, name) if _NAME.fullmatch(name) else None
    try:
        stored = at_rest.unseal(t.secret, SECRET_PURPOSE) if t is not None else None
    except at_rest.SealedSecretUnreadable:
        stored = None   # the at-rest key does not open it: nobody authenticates as this target
    if not stored or not given or not hmac.compare_digest(given.encode(), stored.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="unknown transit target or credential")
    return t


def _actions_for(target: TransitTarget, requested: Optional[List[str]]) -> List[str]:
    allowed = list(target.actions or [])
    if requested is None:
        return allowed
    bad = [a for a in requested if a not in allowed]
    if bad or not requested:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"actions must be a non-empty subset of {allowed}")
    return list(dict.fromkeys(requested))


async def _mint(db: AsyncSession, user: User, target: TransitTarget, *, actions: Optional[List[str]],
                name: Optional[str], expires_in: Optional[int]) -> dict:
    now = datetime.utcnow()
    value = generate_transit_token(target.name)
    tok = APIToken(**stored_fields(value), user_id=user.id, scopes=[], name=name, kind=KIND_KEY,
                   created_at=now, audience=target.name, actions=_actions_for(target, actions),
                   expires_at=now + timedelta(seconds=expires_in) if expires_in else None)
    db.add(tok)
    await db.commit()
    await db.refresh(tok)
    return {**_key_view(tok), "token": value}


def _legacy_row_matches(target: TransitTarget, token: str) -> bool:
    prefixes = set(target.legacy_prefixes or [])
    scope = parse_token_scope(token)
    return (scope or "") in prefixes


def build_router(verify_admin_token) -> APIRouter:
    router = APIRouter()
    admin = [Depends(verify_admin_token)]

    # --- the registry -------------------------------------------------------------------------
    @router.put("/admin/transit-targets/{name}", dependencies=admin)
    async def upsert_target(name: str, body: TargetUpsert, db: AsyncSession = Depends(get_db)):
        if not _NAME.fullmatch(name):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name: [a-z][a-z0-9-]{1,62}")
        if any(not _ACTION.fullmatch(a) for a in body.actions) or \
                any(not _PREFIX.fullmatch(p) for p in body.legacy_prefixes):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="malformed action or prefix")
        t = await db.get(TransitTarget, name)
        if t is None:
            t = TransitTarget(name=name, created_at=datetime.utcnow())
            db.add(t)
        t.actions, t.legacy_prefixes, t.description = list(body.actions), list(body.legacy_prefixes), body.description
        await db.commit()
        await db.refresh(t)
        return _target_view(t)

    @router.get("/admin/transit-targets", dependencies=admin)
    async def list_targets(db: AsyncSession = Depends(get_db)):
        rows = (await db.execute(select(TransitTarget).order_by(TransitTarget.name))).scalars().all()
        return [_target_view(t) for t in rows]

    @router.post("/admin/transit-targets/{name}/secret", dependencies=admin)
    async def issue_target_secret(name: str, db: AsyncSession = Depends(get_db)):
        """The target's credential, answered once. Issuing a new one retires the old at once."""
        t = await _target(db, name)
        value = "vxts_" + secrets.token_urlsafe(32)
        t.secret = at_rest.seal(value, SECRET_PURPOSE)
        t.secret_rotated_at = datetime.utcnow()
        await db.commit()
        return {"name": name, "secret": value}

    # --- keys ---------------------------------------------------------------------------------
    @router.post("/admin/users/{user_id}/transit-keys", dependencies=admin, status_code=status.HTTP_201_CREATED)
    async def mint_for_person(user_id: int, body: TransitKeyCreate, db: AsyncSession = Depends(get_db)):
        user = await db.get(User, user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        return await _mint(db, user, await _target(db, body.audience), actions=body.actions,
                           name=body.name, expires_in=body.expires_in)

    @router.post("/transit/keys", include_in_schema=False, status_code=status.HTTP_201_CREATED)
    async def mint_on_target(body: TargetKeyCreate, target: TransitTarget = Depends(authenticated_target),
                             db: AsyncSession = Depends(get_db)):
        user = await db.get(User, body.user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        return await _mint(db, user, target, actions=body.actions, name=body.name, expires_in=body.expires_in)

    def _on_target(target: TransitTarget, user_id: int):
        """A person's transit keys ON THIS TARGET, and nothing else: a target never sees, lists or
        revokes a person's general keys, legacy ones included (those are the person's or an
        operator's to manage)."""
        return (APIToken.user_id == user_id) & (APIToken.audience == target.name)

    @router.get("/transit/keys", include_in_schema=False)
    async def list_on_target(user_id: int = Query(..., ge=1), target: TransitTarget = Depends(authenticated_target),
                             db: AsyncSession = Depends(get_db)):
        rows = (await db.execute(select(APIToken).where(_on_target(target, user_id))
                                 .order_by(APIToken.id))).scalars().all()
        return [_key_view(t) for t in rows]

    @router.delete("/transit/keys", include_in_schema=False)
    async def revoke_on_target(user_id: int = Query(..., ge=1), key_id: Optional[int] = Query(None),
                               target: TransitTarget = Depends(authenticated_target),
                               db: AsyncSession = Depends(get_db)):
        stmt = delete(APIToken).where(_on_target(target, user_id))
        if key_id is not None:
            stmt = stmt.where(APIToken.id == key_id)
        result = await db.execute(stmt)
        await db.commit()
        return {"revoked": result.rowcount or 0}

    # --- validation -----------------------------------------------------------------------------
    @router.post("/transit/validate", include_in_schema=False)
    async def validate_transit(body: TransitValidate, target: TransitTarget = Depends(authenticated_target),
                               db: AsyncSession = Depends(get_db)):
        row = (await db.execute(
            select(APIToken, User).join(User, APIToken.user_id == User.id)
            .where(token_matches(APIToken, body.token))
        )).first()
        refused = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not a key for this target")
        if row is None:
            raise refused
        tok, user = row
        from . import principals as principals_mod  # noqa: PLC0415
        if principals_mod.is_principal_account(user):
            raise refused   # a workspace principal holds no key (D2), so none opens a target
        if tok.expires_at is not None and tok.expires_at < datetime.utcnow():
            raise refused
        if tok.audience is not None:
            if tok.audience != target.name:
                raise refused
            actions = list(tok.actions or [])
        elif getattr(tok, "kind", KIND_KEY) == KIND_KEY and _legacy_row_matches(target, body.token):
            actions = list(target.actions or [])
        else:
            raise refused
        if body.action not in actions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"this key may not {body.action!r}")
        from .validate import _stamp_last_used  # noqa: PLC0415
        await _stamp_last_used(db, tok)
        secret = at_rest.unseal(target.secret, SECRET_PURPOSE)   # opened above to authenticate
        now = int(time.time())
        # `token_sha256` binds the answer to the key the target presented (an answer cannot be
        # replayed for another key); `nonce` makes every answer unique.
        claims = {"aud": target.name, "sub": user.id, "email": user.email, "actions": actions,
                  "key_id": tok.id, "token_sha256": hash_token(body.token), "nonce": secrets.token_hex(8),
                  "iat": now, "exp": now + ANSWER_TTL_SECONDS}
        return sign_answer(secret, claims)

    return router
