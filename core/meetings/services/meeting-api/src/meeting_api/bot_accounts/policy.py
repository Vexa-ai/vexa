"""Who may use and who may manage a company bot account.

* **Use** — ``VEXA_COMPANY_BOT_USE`` (``all`` | ``admins`` | ``none``, default ``admins``). The
  company's statement of the rule is ``_global/POLICIES.md`` ``company_bot_use``; this env carries it
  to meeting-api until meeting-api reads policies itself. An unknown value reads as ``admins``.
* **Create / delete / sign in again** — only the deployment's admin account (admin-api
  ``admin_account``).

Roles come from admin-api ``GET /internal/users/{id}/is-admin`` (``ADMIN_API_URL`` +
``INTERNAL_API_SECRET``), answered as ``{is_admin, admin_account}``, cached briefly per person.
Every failure — unset URL or secret, unreachable, non-200, a malformed body — is **neither role**
(fail closed), and a failure is never cached.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional, Protocol

POLICY_VALUES = ("all", "admins", "none")
DEFAULT_POLICY = "admins"
ROLES_CACHE_TTL_S = 30.0


def company_bot_use(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    raw = (env.get("VEXA_COMPANY_BOT_USE") or "").strip().lower()
    return raw if raw in POLICY_VALUES else DEFAULT_POLICY


class AdminOracle(Protocol):
    async def roles(self, user_id: int) -> dict:
        """``{"is_admin": bool, "admin_account": bool}``; both False when identity cannot answer."""
        ...


NO_ROLES = {"is_admin": False, "admin_account": False}


class HttpAdminOracle:
    """``AdminOracle`` over admin-api's internal tier, with a short per-person cache."""

    def __init__(self, admin_api_url: str, internal_secret: str, *, client: Any = None,
                 ttl_s: float = ROLES_CACHE_TTL_S, clock: Callable[[], float] = time.monotonic):
        self._url = (admin_api_url or "").rstrip("/")
        self._secret = internal_secret or ""
        self._client = client
        self._ttl = ttl_s
        self._clock = clock
        self._cache: dict[int, tuple[float, dict]] = {}

    @classmethod
    def from_env(cls) -> "HttpAdminOracle":
        return cls(os.getenv("ADMIN_API_URL") or "", os.getenv("INTERNAL_API_SECRET") or "")

    async def roles(self, user_id: int) -> dict:
        if not (self._url and self._secret):
            return dict(NO_ROLES)
        now = self._clock()
        hit = self._cache.get(user_id)
        if hit and hit[0] > now:
            return dict(hit[1])
        url = f"{self._url}/internal/users/{int(user_id)}/is-admin"
        headers = {"X-Internal-Secret": self._secret}
        try:
            if self._client is not None:
                r = await self._client.get(url, headers=headers, timeout=5.0)
            else:
                import httpx

                async with httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return dict(NO_ROLES)
            body = r.json()
        except Exception:  # noqa: BLE001 — fail closed: neither role
            return dict(NO_ROLES)
        if not isinstance(body, dict):
            return dict(NO_ROLES)
        out = {"is_admin": body.get("is_admin") is True,
               "admin_account": body.get("admin_account") is True}
        self._cache[user_id] = (now + self._ttl, out)
        return dict(out)


class FakeAdminOracle:
    """``AdminOracle`` from a dict ``{user_id: {"is_admin": .., "admin_account": ..}}``."""

    def __init__(self, roles: Optional[dict] = None):
        self._roles = dict(roles or {})

    async def roles(self, user_id: int) -> dict:
        return {**NO_ROLES, **self._roles.get(int(user_id), {})}


def owner_of(user_id: int) -> str:
    return f"user:{int(user_id)}"


def usable(account: dict, *, user_id: int, policy: str, roles: dict) -> bool:
    """May this person send a bot as ``account``? Theirs, or a company account the policy opens to
    them (``all``, or ``admins`` and they are an admin or the admin account)."""
    if account.get("owner") == owner_of(user_id):
        return True
    if account.get("owner") != "company":
        return False
    if policy == "all":
        return True
    if policy == "admins":
        return bool(roles.get("is_admin") or roles.get("admin_account"))
    return False
