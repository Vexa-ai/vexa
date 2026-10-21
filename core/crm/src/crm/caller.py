"""Who is calling the CRM, as identity answered it (ADR-0047 decisions 6 and 7).

The CRM does not decide who a credential belongs to. Identity does, at `/internal/validate` — the
resolver the gateway and flows already ask — and the CRM is one more caller of it. The answer
carries everything the CRM's two gates need:

- ``user_id`` and ``email`` — the person (decision 3 attaches their account to a person entity);
- ``email_verified`` — whether a sign-in proved the person holds ``email``. Only a proven address
  matches, attaches or claims a Person record (``store.ensure_account``, ``person_profiles``);
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
    #: identity.v1 ``email_verified``: a sign-in proved the person holds ``email``. False when identity
    #: did not say so (an API-made account, a changed address, an older identity, a bare subject).
    email_verified: bool = False
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
    #: D13: how this caller proved itself, as identity answered it (identity.v1 ``credential_class``):
    #: ``session`` — the terminal's sign-in, the only credential a decision or the seen relation takes;
    #: ``key`` — any API key, a browser-scoped one included; ``delegation`` — a worker's token. Empty
    #: when unknown (an older identity, a bare subject), which is never ``session``.
    credential_class: str = ""
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
    # PRINCIPAL-KIND HOOK (S1-B, PRD D2), wired at integration (int-s1): a WORKSPACE principal (a
    # room's turn; `user_id` is its own numeric account, `delegation.principal_kind == "workspace"`)
    # is shared by construction, even if `shared` is missing, and never the admin. Every field right
    # in store.py keys off `caller.shared`, so a principal reads the room policy (the members'
    # intersection), never the org default (deny test 36).
    ws_principal = dlg.get("principal_kind") == "workspace"
    principal_workspace = str(dlg.get("principal_workspace") or "").strip() if ws_principal else ""
    shared = dlg.get("shared") is True or ws_principal
    # D4: a shared (room) caller is NEVER a CRM administrator, whoever its person is. The room's
    # field rights are computed from its members and the field classification (field_classes.py).
    person_admin = (bool(body.get("is_admin")) or (delegated and bool(body.get("person_is_admin")))) \
        and not ws_principal
    admin = person_admin and not shared
    workspaces = tuple(str(w) for w in (body.get("workspaces") or []) if str(w))
    writable = tuple(str(w) for w in (body.get("writable_workspaces") or []) if str(w) in workspaces)
    on_behalf_of = str(dlg.get("on_behalf_of") or body.get("on_behalf_of") or "").strip()
    # D13: positive evidence only — a delegation is a delegation whatever the body says.
    credential_class = "delegation" if delegated else (
        str(body.get("credential_class") or "") if body.get("credential_class") in ("session", "key") else "")
    return Caller(subject=str(uid), email=str(body.get("email") or "").strip().lower(),
                  email_verified=body.get("email_verified") is True, is_admin=admin,
                  workspaces=workspaces, writable=writable, delegated=delegated,
                  regime=str(dlg.get("regime") or "").strip().lower(), shared=shared,
                  cross_org=shared and dlg.get("audience") == "cross_org",
                  session=str(dlg.get("unit") or ""), trace=trace, credential_class=credential_class,
                  # `person_admin` only decides WHICH refusal a room gets (store._rights); never a grant.
                  extra={**({"on_behalf_of": on_behalf_of} if on_behalf_of else {}),
                         **({"person_admin": True} if shared and person_admin else {}),
                         **({"principal_kind": "workspace", "principal_workspace": principal_workspace}
                            if ws_principal else {})})


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
SESSION_REFUSAL = {
    "status": "refused",
    "reason": "session_required",
    "instruction": "A decision is made by the person at their own terminal, signed in — never with an API "
                   "key or an agent's token. Tell them it is waiting on them and give them the link; do not "
                   "retry it another way.",
}
CONFIRM_REFUSAL = {
    "status": "refused",
    "reason": "person_confirmation_required",
    "instruction": "A person accepts or rejects a proposed change themselves. Tell them it is waiting "
                   "and give them the record's link; do not retry it another way.",
}


def optional(value: Optional[str]) -> str:
    return str(value or "").strip()
