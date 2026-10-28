"""Which CRM org serves a caller (architecture A1: an individual account has its own CRM).

The install's org (``CRM_ORG_ID``) serves everybody, exactly as before, unless ``personal_crm`` is
switched on (``features.personal_crm_on``, explicit, never by ``all``). Then a person whose home org
in identity is their org of one (kind ``personal``, id ``p-<user id>``) is served from THAT org's
rows, and only from them: their personal CRM. Its first use creates the CRM org with the person as
its only administrator.

Identity decides the org, never the caller: crm-api asks ``GET /internal/users/{id}/org`` with the
internal secret. When identity cannot answer, the request fails (503). It never falls back to the
install org, which would serve an individual the company's records.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

import httpx

from . import features
from .caller import Caller, IdentityUnavailable

ORG_PATH = "/internal/users/{subject}/org"
PERSONAL = "personal"
PERSONAL_PREFIX = "p-"
#: The only org shape the erase door accepts: an individual's org of one.
PERSONAL_ORG = re.compile(r"^p-[0-9]+$")
TTL_S = 30.0
TIMEOUT_S = 5.0

#: The personal CRM's default policy: its one member administers it (no one else is ever served it).
PERSONAL_POLICY = {"active": True, "admin": True}


class OrgResolver:
    def __init__(self, install_org: str, base: str = "", secret: str = "", *, transport=None,
                 ensure_org: Optional[Callable[[str, str], None]] = None, clock=time.monotonic):
        self.install_org = install_org
        self._base = base.rstrip("/")
        self._secret = secret
        self._transport = transport
        self._ensure_org = ensure_org
        self._clock = clock
        self._cache: dict[str, tuple[float, str]] = {}
        self._made: set[str] = set()

    def org_for(self, caller: Caller) -> str:
        if caller.internal or not caller.subject or not features.personal_crm_on():
            return self.install_org
        now = self._clock()
        hit = self._cache.get(caller.subject)
        if hit and now - hit[0] < TTL_S:
            org = hit[1]
        else:
            org = self._ask(caller.subject)
            self._cache[caller.subject] = (now, org)
        if org != self.install_org and org not in self._made and self._ensure_org is not None:
            self._ensure_org(org, caller.email or org)
            self._made.add(org)
        return org

    def _ask(self, subject: str) -> str:
        if not self._base:
            raise IdentityUnavailable("no identity to ask for the caller's org")
        try:
            with httpx.Client(transport=self._transport, timeout=TIMEOUT_S) as client:
                r = client.get(self._base + ORG_PATH.format(subject=subject),
                               headers={"X-Internal-Secret": self._secret})
        except httpx.HTTPError as exc:
            raise IdentityUnavailable(type(exc).__name__) from exc
        if r.status_code != 200:
            raise IdentityUnavailable(f"identity answered {r.status_code} for the caller's org")
        try:
            org = (r.json() or {}).get("org") or {}
        except ValueError as exc:
            raise IdentityUnavailable("identity answered a body that is not JSON") from exc
        oid = str(org.get("id") or "")
        # Only an org of one that is THIS person's (p-<their id>) is served as a personal CRM; any
        # other answer (a company org, an org row missing) is the install org, as before.
        if org.get("kind") == PERSONAL and oid == f"{PERSONAL_PREFIX}{subject}":
            return oid
        return self.install_org
