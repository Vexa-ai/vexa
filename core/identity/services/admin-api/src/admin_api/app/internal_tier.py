"""The internal tier — the two `X-Internal-Secret` checks every `/internal/*` door of admin-api uses.

`INTERNAL_API_SECRET` is shared by the services that call identity from inside the deployment (the
gateway, agent-api, meeting-api, flows, the terminal's server). Both checks read the environment on
every call, so a test or an operator sees the value in force now, not the one at import.

  * `check_internal` FAILS CLOSED: no secret configured → 503, a wrong or missing header → 403. With
    `DEV_MODE=true` and no secret it lets the call through — a local-development convenience for
    doors whose answer the caller could get anyway (`/internal/validate`, the membership index).
  * `check_internal_no_dev_bypass` has no dev-mode escape. It guards a door that reads or writes ONE
    NAMED PERSON'S data by an id the caller supplies, or that answers whether an address has an
    account: there the bypass would be a cross-user read (or write) with no credential at all.
"""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, status


def internal_secret() -> str:
    return os.environ.get("INTERNAL_API_SECRET", "")


def dev_mode() -> bool:
    return os.getenv("DEV_MODE", "false").lower() == "true"


def check_internal(request: Request) -> None:
    secret = internal_secret()
    if not dev_mode() and not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="INTERNAL_API_SECRET not configured")
    if secret:
        provided = request.headers.get("X-Internal-Secret", "")
        if not hmac.compare_digest(provided, secret):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid internal secret")


def check_internal_no_dev_bypass(request: Request) -> None:
    """The internal check WITHOUT the dev-mode escape — for a door that reads or writes ONE NAMED
    PERSON'S data by path id.

    `check_internal` lets `DEV_MODE=true` with no `INTERNAL_API_SECRET` through unauthenticated. For
    the doors it was written for — `/internal/validate`, the membership index — that is a
    local-development convenience over data the caller could get anyway. For a route shaped
    `/internal/users/{id}/…` it is not the same thing: the id is supplied by the CALLER, so the bypass
    is a cross-user read (or write) of somebody's private preferences with no credential at all. The
    two cases have opposite blast radii and had one check, which is how the weaker one ended up
    guarding the stronger door.

    Dev mode still works; it simply has to name a secret first — a one-line change to a compose file
    against a route that otherwise answers for any person on the instance."""
    secret = internal_secret()
    if not secret:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("INTERNAL_API_SECRET not configured — this door reads/writes one named "
                    "person's settings and is never open, dev mode included"))
    provided = request.headers.get("X-Internal-Secret", "")
    if not hmac.compare_digest(provided, secret):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid internal secret")
