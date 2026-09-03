"""WHO IS CALLING — resolved from the caller's own credential, by the service that owns the answer.

Flows' whole surface used to sit behind one deployment-wide operator key. That key is not a person:
with it `GET /reactions` returns every reaction in the instance and `POST /reactions/{id}/cancel`
cancels any of them. So the MCP edge — which forwards the CALLER's credential and holds none of its
own — could be wired two ways and both were wrong: refuse the person, or hand them the instance.

This module is the third way, and it is deliberately NOT a new answer to "who is this". Identity
owns that answer and publishes it at `/internal/validate`; the gateway is the caller everyone knows
about (`core/gateway/services/gateway/src/gateway/adapters.py:85`). Flows is now a SECOND CALLER of
that ONE resolver, not a second resolver — the distinction P23 is about. Nothing here parses a
token, reads a users table, or decides what a scope means.

THREE ANSWERS, AND THE THIRD IS THE ONE THAT GETS CONFUSED:

    identity says user 126        ->  Caller(kind="subject", uid="126", email=…)
    identity says "invalid token" ->  SubjectUnknown            -> 401, the caller's problem
    identity does not answer,     ->  IdentityUnavailable       -> 503, OUR problem
      or refuses OUR secret

The third is the whole reason this is a module and not four lines in the route. An oracle that
cannot be reached has NOT reached a verdict on the credential, and reporting one is how #495/#483
turned a gateway hiccup into "your API key is invalid" for every user at once. `adapters.py:99-101`
records that lesson at the gateway; this is the same rule at flows' door, including the case the
gateway does not have: identity answering 403 because OUR internal secret is wrong is a deployment
fault, and telling the person their key is invalid sends them to rotate a key that works.
"""
from __future__ import annotations

from dataclasses import dataclass

import flows_config

#: identity's authz oracle — the route, not a route. Same path the gateway posts to.
VALIDATE_PATH = "/internal/validate"
#: how long we wait for it. The gateway waits 5s for the same hop; a door check that takes longer
#: than the call it guards is its own outage.
TIMEOUT_S = 5.0


class IdentityUnavailable(Exception):
    """Identity could not be reached, or refused US. NOT a verdict on the caller's credential."""


class SubjectUnknown(Exception):
    """Identity answered, and nobody answers to this credential."""


@dataclass(frozen=True)
class Caller:
    """Who is on the other end of this request, and in which of the two tiers.

    ``admin`` is the operator key: unscoped, every route, the behaviour this service has always
    had. ``subject`` is one person, and every subject-scoped route derives its subject from
    ``uid`` — never from an argument the caller sent.
    """
    kind: str
    uid: str = ""
    email: str = ""

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"

    @property
    def names(self) -> set:
        """Every spelling of this caller, lower-cased — the uid and the address.

        Both, because a client that already holds one of them will send whichever it has, and the
        two lineages this service scopes on carry different ones (`flows_timeline.model.concerns`).
        """
        return {s for s in (self.uid.strip().lower(), self.email.strip().lower()) if s}


def _validate(base: str, token: str, secret: str):
    """The one HTTP hop, alone in a function so a test can replace exactly it.

    `flows_steps.common.http` rather than httpx: flows has no httpx at runtime and reaches every
    service over urllib — declaring a wheel for one POST would make this module the reason the
    image grows.
    """
    from flows_steps.common import http
    return http("POST", f"{base}{VALIDATE_PATH}", {"X-Internal-Secret": secret}, {"token": token},
                timeout=TIMEOUT_S)


def resolve(token: str, *, secret: str = "") -> Caller:
    """The person this credential belongs to. Raises :class:`SubjectUnknown` or
    :class:`IdentityUnavailable` — never returns a caller it is not sure about.

    `secret` is the internal-tier credential the CALLER already read at boot — flows-api holds it
    as a module constant precisely so an unconfigured deployment stops at import rather than at the
    first request. Passed in rather than re-read here so there is one read of it in the process,
    and so a missing one is a boot refusal and never a per-request 503.
    """
    token = str(token or "").strip()
    if not token:
        raise SubjectUnknown("no credential")
    if not secret:
        from flows_steps.common import require_internal_secret
        secret = require_internal_secret()
    base = flows_config.require("VEXA_FLOWS_ADMIN_API_URL").rstrip("/")
    try:
        code, body = _validate(base, token, secret)
    except Exception as e:  # noqa: BLE001 — transport, DNS, a refused connection: all one answer
        raise IdentityUnavailable(f"{type(e).__name__}: {e}"[:200]) from e

    code = int(code or 0)
    if code == 401:
        raise SubjectUnknown("identity does not recognise this credential")
    if code != 200 or not isinstance(body, dict):
        # 403 = our internal secret; 503 = identity unconfigured; anything else = not a verdict.
        raise IdentityUnavailable(f"identity answered {code} to the token check")
    uid = body.get("user_id")
    if uid in (None, ""):
        raise IdentityUnavailable("identity answered 200 with no user_id")
    return Caller(kind="subject", uid=str(uid), email=str(body.get("email") or ""))
