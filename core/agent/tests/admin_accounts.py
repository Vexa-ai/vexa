"""Who the ADMIN ACCOUNT is, in an L2 test — identity's answer, faked at agent-api's one reader.

Production asks admin-api (`global_layer.is_admin_account` → `/internal/users/{id}/is-admin`, field
`admin_account`); there is no list in agent-api any more. A test that needs an admin account names it
here (`name("u_admin")`) and uses the `fake_admin_accounts` fixture (conftest), which clears the set
and points `global_layer.is_admin_account` at it for that one test.
"""
from __future__ import annotations

NAMED: set[str] = set()


def name(admins: str = "") -> None:
    """Name the admin account(s) for the running test — comma-separated subjects, "" for nobody."""
    NAMED.update(a.strip() for a in str(admins or "").split(",") if a.strip())


def is_admin_account(settings, subject) -> bool:
    return str(subject) in NAMED
