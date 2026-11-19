"""THE ADMIN ACCOUNT — the one identity that controls the company layer, `_global`.

Founder ruling, 2026-10-11: *"let's change so that admin is a special account that has global only
and this being the only one that has control to global"*.

WHAT IT IS. A dedicated account, not a person's everyday one. It is the only identity that writes
`_global` (files, flows, `POLICIES.md`, its reset), and it does nothing else: no desk, no shared
workspaces, no meetings. Everybody else — the instance admin who edits Settings included — reads
`_global` and never writes it.

HOW AN ACCOUNT IS ONE. Both halves, always:

  1. the deployment names its address in ``VEXA_ADMIN_ACCOUNT`` (comma-separated exact addresses;
     normally one), and
  2. the user row was CREATED as the admin account: ``users.data.account == "admin"``, stamped in
     the same transaction that created the row, and only when (1) already named the address.

The stamp is written in exactly one place (``POST /admin/users``, the point every sign-in door
creates an account) and never on a row that already exists. So naming an address that already
belongs to somebody's everyday account does NOT turn that account into the admin account — an
existing personal account is never promoted, and the boot says so (:func:`conflicts_message`).
Removing the address from the env revokes the account at the next restart; the stamp alone is not
enough. Nothing configured means no admin account, and then nobody writes `_global`.

WHY NOT A ROLE ON AN EXISTING ACCOUNT. The instance admin (``users.data.is_admin``, claimed at first
sign-in) is a person: it is the founder's own account on every instance we run. A role that took the
desk and meetings away from whoever held it could not be granted to a person at all, and a role that
left them would make the person's everyday chats able to rewrite what every agent carries — the
thing the ruling removes. So the admin account is its own kind of account, created for the purpose.

WHO READS IT. This service answers it on its existing doors and nothing else decides it:
``/internal/validate`` (``admin_account`` / ``person_admin_account``, identity.v1), which the gateway
reports on ``/auth/me`` and the terminal reads; and ``/internal/users/{id}/is-admin``
(``admin_account``), agent-api's one reader. Its bot cap is always 0 (:data:`MAX_CONCURRENT_BOTS`),
so meeting-api refuses it a bot by the rule it already enforces for a depleted quota.

THIS MODULE IS PURE — every decision is provable without a database.
"""
from __future__ import annotations

import os
from typing import Iterable, List, Tuple

from . import signin_allow

ENV_KEY = "VEXA_ADMIN_ACCOUNT"
#: The value of ``users.data.account`` on the admin account's row.
ACCOUNT_FIELD = "account"
ACCOUNT_ADMIN = "admin"
#: The admin account launches no bot. 0 is meeting-api's "quota depleted" (`max_concurrent <= 0`).
MAX_CONCURRENT_BOTS = 0
#: At most this many addresses. The design is one per deployment; a short list covers a rotation.
MAX_ADDRESSES = 5


def _problem(entry: str) -> str | None:
    if entry == signin_allow.WILDCARD:
        return f"{entry!r} is everyone — {ENV_KEY} names the admin account, one full address"
    if entry.startswith("@"):
        return f"{entry!r} is a domain — {ENV_KEY} names the admin account, one full address"
    return signin_allow.entry_problem(entry)


def designated() -> Tuple[List[str], List[str]]:
    """The addresses ``VEXA_ADMIN_ACCOUNT`` names, read on every call: (valid, one sentence per
    problem). Exact, case-folded addresses; a domain or ``*`` is refused, never widened."""
    valid: List[str] = []
    problems: List[str] = []
    for entry in signin_allow.split_entries(os.getenv(ENV_KEY, "")):
        problem = _problem(entry)
        if problem:
            problems.append(problem)
        elif entry not in valid:
            valid.append(entry)
    if len(valid) > MAX_ADDRESSES:
        problems.append(f"{len(valid)} addresses — at most {MAX_ADDRESSES}; the admin account is "
                        "one dedicated account, not a list of people")
    return valid, problems


def boot_problems() -> List[str]:
    """Every malformed entry, each naming the key. The boot refuses on any of them: a typo here
    would leave the deployment without the admin account it names, silently."""
    return [f"{ENV_KEY}: {p}" for p in designated()[1]]


def is_stamped(data) -> bool:
    """Was this row created as the admin account?"""
    return isinstance(data, dict) and data.get(ACCOUNT_FIELD) == ACCOUNT_ADMIN


def is_admin_account(email, data, names: Iterable[str] | None = None) -> bool:
    """THE test: created as the admin account AND still named by the deployment. Fail closed —
    either half missing is no."""
    names = designated()[0] if names is None else list(names)
    return is_stamped(data) and signin_allow.normalize_email(email) in set(names)


def stamp_for_new_row(email, data: dict, names: Iterable[str] | None = None) -> dict:
    """The ``data`` a NEW row is created with: stamped when the deployment names this address.
    Called only on the create path — an existing row is never passed here."""
    names = designated()[0] if names is None else list(names)
    if signin_allow.normalize_email(email) in set(names):
        return {**data, ACCOUNT_FIELD: ACCOUNT_ADMIN}
    return data


def conflicts_message(conflicts: Iterable[str]) -> str:
    """The boot's warning for named addresses that already belong to an everyday account."""
    return (f"{ENV_KEY} names {', '.join(sorted(conflicts))}, which already belong(s) to an everyday "
            "account. An existing account is never turned into the admin account, so nobody writes "
            "_global through it: name a fresh address that has never signed in here.")
