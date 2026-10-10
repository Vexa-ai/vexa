"""provider_subjects.py — an account is bound to the OAuth identity that first signed in to it.

Accounts here are keyed by email. An OAuth sign-in may use the provider's email only when the
provider verified it (the terminal's ``providerIdentity.ts``), but inside one Microsoft tenant the
tenant's administrator decides what ``email`` says for any of its users, and Google's ``sub`` is the
only stable thing about a Google account. So the first OAuth sign-in to an account records the
provider's stable subject on it, and every later one through that provider must carry the same:

    users.data["provider_subjects"] = {"google": "google:<sub>",
                                       "microsoft": "microsoft:<tid>:<oid>",
                                       "oidc": "oidc:<sha256 hex of issuer and sub>"}

The generic OIDC door (ADFS, Keycloak) hashes the issuer with ``sub`` because ``sub`` is unique only
within its issuer and an ADFS ``sub`` is base64; the hash gives one fixed alphabet, and a different
issuer never produces the same subject.

One subject per provider. A sign-in carrying a different subject for an already-bound provider is
refused (signin.v1 ``ProviderSubjectBindRequest``; the door is ``PUT
/internal/users/{id}/provider-subject`` in ``main.py``). The emailed link does not pass through here:
holding the mailbox is its proof.
"""
from __future__ import annotations

import re
from typing import Literal

#: The key in ``users.data`` that holds the bindings.
DATA_KEY = "provider_subjects"

_GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
#: The subjects the terminal's providerIdentity.ts produces, and nothing else.
SUBJECT_RE = re.compile(rf"^(?:google:[A-Za-z0-9._-]{{1,255}}|microsoft:{_GUID}:{_GUID}|oidc:[0-9a-f]{{64}})$")


class Mismatch(Exception):
    """The account is bound to another subject of the same provider."""


def provider_of(subject: str) -> str:
    return subject.split(":", 1)[0]


def bind(data: dict, subject: str) -> tuple[dict, Literal["first", "same"]]:
    """The user's ``data`` with ``subject`` bound, and whether this was the first binding for its
    provider. Raises :class:`Mismatch` when that provider is bound to another subject, and
    ``ValueError`` for a subject that is not one the terminal produces."""
    if not isinstance(subject, str) or not SUBJECT_RE.match(subject):
        raise ValueError("not a provider subject")
    current = data.get(DATA_KEY) if isinstance(data.get(DATA_KEY), dict) else {}
    provider = provider_of(subject)
    bound = current.get(provider)
    if bound == subject:
        return data, "same"
    if bound:
        raise Mismatch(provider)
    return {**data, DATA_KEY: {**current, provider: subject}}, "first"
