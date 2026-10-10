"""signin_links.py — an emailed sign-in link signs in once, across every terminal replica.

The terminal mints the link (a signed token carrying an email, an expiry and a random ``jti``) and
verifies the signature and expiry itself. What it cannot do on its own is remember that a ``jti`` was
already redeemed: a record in one process is not seen by another replica, nor by the same one after
a restart. So the terminal asks here, and the record lives in the service Redis until the link would
have expired anyway (signin.v1 ``SigninLinkRedeemRequest``):

    vexa:signin-link:redeemed:<jti>   "1"   (SET NX, expiring at the link's own expiry)

``POST /internal/signin-links/redeem`` answers 200 ``{"first": true}`` only to the request that
created the record; every later one, and any link already past its expiry, gets 409.

FAILS CLOSED. A store that is unset, unreachable or erroring answers 503, and the terminal refuses the
sign-in (the link is not spent; the person can try again). Never "first", which would let a link be
used twice whenever Redis is down.

Internal tier without the dev-mode escape: a caller that could reach this unauthenticated could spend
somebody else's link by its jti.
"""
from __future__ import annotations

import os
import time
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from .internal_tier import check_internal_no_dev_bypass

#: The record for a redeemed link: ``REDEEMED_PREFIX + jti``.
REDEEMED_PREFIX = "vexa:signin-link:redeemed:"
#: The longest a link the terminal mints can live (its MAX_TTL_SECONDS); a record is never kept
#: longer, whatever expiry a caller names.
MAX_RECORD_SEC = 3600
#: Where the store is when ``REDIS_URL`` is unset (config.v1 ``REDIS_URL``).
DEFAULT_REDIS_URL = "redis://redis:6379/0"
#: A Redis that does not answer within this many seconds is an unavailable store.
TIMEOUT_SEC = 2.0

_client: Optional[Any] = None


def _redis():
    global _client
    if _client is None:
        import redis.asyncio as aioredis

        _client = aioredis.from_url(os.environ.get("REDIS_URL") or DEFAULT_REDIS_URL,
                                    socket_connect_timeout=TIMEOUT_SEC, socket_timeout=TIMEOUT_SEC)
    return _client


class SigninLinkRedeemRequest(BaseModel):
    """signin.v1 ``SigninLinkRedeemRequest``."""
    model_config = {"extra": "forbid"}

    jti: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")
    expires_at: int


class SigninLinkRedeemResponse(BaseModel):
    """signin.v1 ``SigninLinkRedeemResponse``."""
    first: Literal[True]


router = APIRouter()


@router.post("/internal/signin-links/redeem", include_in_schema=False,
             response_model=SigninLinkRedeemResponse)
async def redeem_signin_link(payload: SigninLinkRedeemRequest, request: Request):
    check_internal_no_dev_bypass(request)
    remaining = payload.expires_at - int(time.time())
    if remaining <= 0:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="this sign-in link has expired")
    try:
        first = await _redis().set(REDEEMED_PREFIX + payload.jti, "1", nx=True,
                                   ex=min(remaining, MAX_RECORD_SEC))
    except Exception as e:  # noqa: BLE001 — every failure means "cannot record", which refuses
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"sign-in link record unavailable ({type(e).__name__})") from e
    if not first:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="this sign-in link was already used")
    return {"first": True}
