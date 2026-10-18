"""Who is calling the CRM, as identity answered it (ADR-0047 decisions 6 and 7).

The CRM does not decide who a credential belongs to. Identity does, at `/internal/validate` — the
resolver the gateway and flows already ask — and the CRM is one more caller of it. The answer
carries everything the CRM's two gates need:

- ``user_id`` and ``email`` — the person (decision 3 attaches their account to a person entity);
- ``is_admin`` / ``person_is_admin`` — an instance administrator is a CRM administrator;
- ``workspaces`` — the person's live memberships, already narrowed to a worker's ceiling. This is
  the membership half of a derived grant, and in a turn other people read it is the participants'
  intersection (ADR-0048 decision 4);
- ``delegation`` — present for a worker's token: its ``regime`` (``human`` means the person is in
  the loop), ``workspaces`` ceiling, ``shared`` flag and ``unit`` (the session the trace names).

Three answers, kept apart exactly as flows keeps them: a caller, an unknown credential (401, the
caller's problem) and an identity that cannot answer (503, ours).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import httpx

VALIDATE_PATH = "/internal/validate"
ACCEPTS_DELEGATION_HEADER = "x-vexa-internal-accepts-delegation"
DELEGATION_TOKEN_PREFIX = "vxd_"
TIMEOUT_S = 5.0


@dataclass(frozen=True)
class Caller:
    subject: str
    email: str = ""
    is_admin: bool = False
    #: Live memberships narrowed to the ceiling. Empty for a caller identity gave none.
    workspaces: tuple = ()
    #: The memberships that may write (contributor or owner), as identity answered them — narrowed
    #: to a worker's ceiling like `workspaces`. None when the caller did not come from identity.
    writable: Optional[tuple] = None
    delegated: bool = False
    regime: str = ""
    shared: bool = False
    #: A shared turn read by people from outside the org (delegation.v1 scope.audience): its reads
    #: answer each record's public fields only (`store.PUBLIC_FIELDS`), never its narrative.
    cross_org: bool = False
    session: str = ""
    trace: str = ""
    #: An internal caller (the flows pack, the migrations path) acting with no person behind it.
    internal: bool = False
    #: The CRM org this caller is served from: set by crm-api per request (``orgs.OrgResolver``),
    #: the install org unless the caller is an individual with ``personal_crm`` on. Empty = unset.
    org: str = ""
    extra: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def unwatched(self) -> bool:
        """A worker with nobody in the loop: it may read and propose, never write or review."""
        return self.delegated and self.regime != "human"

    @property
    def kind(self) -> str:
        if self.internal:
            return "internal"
        if self.delegated:
            return "agent-shared" if self.shared else ("agent" if self.regime == "human" else "agent-unwatched")
        return "person"


def as_caller(actor) -> Caller:
    """A bare subject string is a person with no memberships — the importer's and the tests' form."""
    return actor if isinstance(actor, Caller) else Caller(subject=str(actor))


class IdentityUnavailable(Exception):
    """Identity could not be reached, or refused our internal secret. Not a verdict on the caller."""


class SubjectUnknown(Exception):
    """Identity answered, and nobody answers to this credential."""


def from_validate(token: str, body: dict, *, trace: str = "") -> Caller:
    uid = body.get("user_id")
    if uid in (None, ""):
        raise IdentityUnavailable("identity answered with no user_id")
    dlg = body.get("delegation")
    delegated = isinstance(dlg, dict) or str(token).startswith(DELEGATION_TOKEN_PREFIX)
    dlg = dlg if isinstance(dlg, dict) else {}
    admin = bool(body.get("is_admin")) or (delegated and bool(body.get("person_is_admin")))
    workspaces = tuple(str(w) for w in (body.get("workspaces") or []) if str(w))
    writable = tuple(str(w) for w in (body.get("writable_workspaces") or []) if str(w) in workspaces)
    return Caller(subject=str(uid), email=str(body.get("email") or "").strip().lower(), is_admin=admin,
                  workspaces=workspaces, writable=writable, delegated=delegated,
                  regime=str(dlg.get("regime") or "").strip().lower(), shared=dlg.get("shared") is True,
                  cross_org=dlg.get("shared") is True and dlg.get("audience") == "cross_org",
                  session=str(dlg.get("unit") or ""), trace=trace)


def resolve(base: str, secret: str, token: str, *, trace: str = "", transport=None) -> Caller:
    token = str(token or "").strip()
    if not token:
        raise SubjectUnknown("no credential")
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_S) as client:
            response = client.post(base.rstrip("/") + VALIDATE_PATH,
                                   headers={"X-Internal-Secret": secret, ACCEPTS_DELEGATION_HEADER: "1"},
                                   json={"token": token})
    except httpx.HTTPError as exc:
        raise IdentityUnavailable(type(exc).__name__) from exc
    if response.status_code == 401:
        raise SubjectUnknown("identity does not recognise this credential")
    if response.status_code != 200:
        raise IdentityUnavailable(f"identity answered {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise IdentityUnavailable("identity answered a body that is not JSON") from exc
    if not isinstance(body, dict):
        raise IdentityUnavailable("identity answered a body that is not an object")
    return from_validate(token, body, trace=trace)


#: The bodies a worker reads whichever service refused it (gateway-identity.v1, word for word).
REFUSAL = {
    "status": "refused",
    "reason": "human_session_required",
    "instruction": "This session runs without a person in the loop. Record what you wanted to do "
                   "and stop; do not retry it another way.",
}
SHARED_REFUSAL = {
    "status": "refused",
    "reason": "shared_conversation",
    "instruction": "Other people read this conversation, so its answer may use only what every one "
                   "of them can read. Say you cannot use it here, and offer to answer in the "
                   "person's private chat; do not retry it another way.",
}
CONFIRM_REFUSAL = {
    "status": "refused",
    "reason": "person_confirmation_required",
    "instruction": "A person accepts or rejects a proposed change themselves. Tell them it is waiting "
                   "and give them the record's link; do not retry it another way.",
}


def optional(value: Optional[str]) -> str:
    return str(value or "").strip()
