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

A WORKER ACTING FOR A PERSON is a fourth answer folded into the first. Identity answers a worker's
delegation token (`vxd_`) only to a resolver that declares it reads the delegation
(`ACCEPTS_DELEGATION_HEADER`), and flows does: the person comes back with `delegation` — the
dispatch's `regime`, `workspaces` and `target` — and `Caller` carries it. A worker whose regime is
not `human` runs with nobody in the loop (`Caller.is_unwatched`, the rule gateway-identity.v1's
`identity_token.is_unwatched` states for the services behind the gateway), and flows-api refuses it
the verbs that act as the person with `REFUSAL`, the body every other service answers it with.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Optional

import flows_config

#: identity's authz oracle — the route, not a route. Same path the gateway posts to.
VALIDATE_PATH = "/internal/validate"
#: how long we wait for it. The gateway waits 5s for the same hop; a door check that takes longer
#: than the call it guards is its own outage.
TIMEOUT_S = 5.0

#: The header flows sets, to `ACCEPTS_DELEGATION_VALUE`, on that hop to declare that it reads a
#: delegation answer (identity.v1 `AcceptsDelegationHeader`). Without it identity refuses a worker's
#: token like one nobody holds.
ACCEPTS_DELEGATION_HEADER = "x-vexa-internal-accepts-delegation"
ACCEPTS_DELEGATION_VALUE = "1"
#: The prefix every delegation token carries (delegation.v1 `PREFIX`). A `vxd_` bearer is a worker's
#: even if identity's answer lost its `delegation`: that answer cannot turn it into the person's key.
DELEGATION_TOKEN_PREFIX = "vxd_"

#: What a verb that needs a person in the loop answers a worker running without one — the body
#: gateway-identity.v1's `identity_token.REFUSAL` is, so a worker reads one refusal whichever service
#: it hit. flows-api is not behind the gateway's signed identity, so it holds a copy, and
#: gate:fact-parity holds the copy to the contract's.
REFUSAL = {
    "status": "refused",
    "reason": "human_session_required",
    "instruction": "This session runs without a person in the loop. Record what you wanted to do "
                   "and stop; do not retry it another way.",
    "remedy": "ask_in_chat",
    "tell_your_person": "This ran without you present (a scheduled routine, a background flow or a "
                        "meeting run), and mail, calendar and connection actions only work when you "
                        "ask in chat. Nothing is wrong with your connection, and signing in again "
                        "will not change it.",
}


class IdentityUnavailable(Exception):
    """Identity could not be reached, or refused US. NOT a verdict on the caller's credential."""


class SubjectUnknown(Exception):
    """Identity answered, and nobody answers to this credential."""


#: The caller kinds that carry NO identity of their own and therefore must NAME the person every
#: subject-scoped read is about. `is_admin` is the operator key; `timeline` is
#: `VEXA_FLOWS_TIMELINE_KEY`, which opens `GET /timeline` for a named subject and nothing else.
#: Neither has a uid, so neither can be compared against a resource — which is exactly why an
#: instance-wide default was the P20 hole: with no subject there was nothing left to authorize.
MEDIATED_KINDS = ("admin", "timeline")


@dataclass(frozen=True)
class Caller:
    """Who is on the other end of this request, and in which of the three tiers.

    ``admin`` is the operator key: every route, and — since the P20 fix — never an instance-wide
    read: it must name the subject it is asking about. ``subject`` is one person, and every
    subject-scoped route derives its subject from ``uid``, never from an argument the caller sent.
    ``timeline`` is the narrow read-only key: it names a subject like the operator does, opens
    `GET /timeline` and nothing else, and is deliberately NOT an admin — it used to be one, which
    silently made "a key that can do exactly one thing" a key that read any person's queue and
    minted a gateway token on their account.
    """
    kind: str
    uid: str = ""
    email: str = ""
    #: A worker's ceiling, as identity answered it (`regime`, `workspaces`, `target`) — present only
    #: when the credential is a worker's delegation token, `{}` when that answer lost it. `None` is a
    #: person's own key, the operator key and the timeline key.
    delegation: Optional[Mapping] = field(default=None, hash=False)

    @property
    def is_delegated(self) -> bool:
        """A worker acting for the person `uid` names, not the person."""
        return self.delegation is not None

    @property
    def regime(self) -> str:
        """The dispatch's regime, `""` when it has none. `human` means the person is in the loop."""
        return str((self.delegation or {}).get("regime") or "").strip().lower()

    @property
    def is_unwatched(self) -> bool:
        """A worker whose regime is not `human` — empty, unknown and `autonomous` alike: nobody is in
        the loop this turn. `identity_token.is_unwatched`'s rule, read from identity's answer."""
        return self.is_delegated and self.regime != "human"

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"

    @property
    def is_timeline(self) -> bool:
        """The narrow read-only key. NOT an operator: it opens one route, for one named subject."""
        return self.kind == "timeline"

    @property
    def must_name_a_subject(self) -> bool:
        """True for the credentials that are not a person — they carry no subject of their own, so
        they have to say whose rows they are asking for (P20: no instance-wide default)."""
        return self.kind in MEDIATED_KINDS

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
    headers = {"X-Internal-Secret": secret, ACCEPTS_DELEGATION_HEADER: ACCEPTS_DELEGATION_VALUE}
    return http("POST", f"{base}{VALIDATE_PATH}", headers, {"token": token}, timeout=TIMEOUT_S)


def _delegation_of(token: str, body: Mapping) -> Optional[dict]:
    """The worker's ceiling, `{}` for a worker's token whose answer lost it, `None` for a person."""
    dlg = body.get("delegation")
    if isinstance(dlg, Mapping):
        return dict(dlg)
    return {} if token.startswith(DELEGATION_TOKEN_PREFIX) else None


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
    return Caller(kind="subject", uid=str(uid), email=str(body.get("email") or ""),
                  delegation=_delegation_of(token, body))
