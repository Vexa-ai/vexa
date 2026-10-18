"""Which address ranges a STORED customer webhook URL may still reach — ``VEXA_WEBHOOK_STORED_URL_POLICY``.

0.12.28's webhook guard refused loopback, private (10/8, 172.16/12, 192.168/16), link-local and
cloud metadata, multicast and a list of internal service names. 0.13 holds webhook URLs to the
outbound-url.v1 guard (``ssrf.py``), which ALSO refuses shared address space (100.64/10),
documentation, benchmarking and protocol-assignment ranges. ``admin-api`` applies that guard when a
URL is SAVED, so every URL saved on 0.13 passed it; a URL saved on 0.12 may not have, and 0.12
delivered to it. Under the 0.13 guard alone every delivery to such a URL is recorded ``blocked`` and
the customer is not told.

* ``v012`` (default) — those newly refused public-use ranges stay reachable for delivery, exactly as
  on 0.12. Everything 0.12 refused stays refused, and so does everything the 0.13 guard refuses for
  reaching this host or its cloud: loopback, link-local, cloud metadata (including 100.100.100.200),
  unspecified, multicast and reserved addresses in ANY form (IPv4-mapped, NAT64, 6to4, …), plus
  ``localhost``, metadata and single-label host names. The connection is still pinned to the checked
  address and re-checked at connect. Each such delivery logs ``webhook_stored_url_v012``.
* ``strict`` — the 0.13 guard alone.

Only per-client deliveries ask this; saving a URL, system webhooks and every other outbound fetch
are unchanged. Mechanically it is an operator allowance (``ssrf.OperatorAllowance``) built here from a
fixed list, never from anything a user supplies.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from typing import Any, Callable, List, Optional

from . import ssrf

POLICY_ENV = "VEXA_WEBHOOK_STORED_URL_POLICY"
POLICIES = ("v012", "strict")
DEFAULT_POLICY = "v012"

log = logging.getLogger(__name__)

Resolver = Callable[[str], List[str]]

#: The ranges 0.13 refuses and 0.12.28 delivered to, less every never-allowed address in them
#: (100.100.100.200 is a cloud metadata address, so it is cut out of 100.64/10).
_V012_ONLY_NETWORKS = (
    *ipaddress.ip_network("100.64.0.0/10").address_exclude(ipaddress.ip_network("100.100.100.200/32")),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)

V012_ALLOWANCE = ssrf.parse_allowance(" ".join(str(n) for n in _V012_ONLY_NETWORKS),
                                      what=POLICY_ENV)


def policy(env: Optional[dict] = None) -> str:
    """The configured policy; an unknown value reads as the default and is logged."""
    raw = ((env if env is not None else os.environ).get(POLICY_ENV) or DEFAULT_POLICY).strip().lower()
    if raw not in POLICIES:
        log.warning("%s: unknown value ignored, using %s", POLICY_ENV, DEFAULT_POLICY)
        return DEFAULT_POLICY
    return raw


def allowance(env: Optional[dict] = None) -> Optional[ssrf.OperatorAllowance]:
    """The allowance a per-client delivery is checked with: ``V012_ALLOWANCE`` under ``v012``."""
    return V012_ALLOWANCE if policy(env) == "v012" else None


def delivers_under_v012(url: str, resolver: Optional[Resolver] = None,
                        env: Optional[dict] = None) -> bool:
    """True when the 0.13 guard refuses ``url`` but the ``v012`` policy admits it."""
    allow = allowance(env)
    if allow is None:
        return False
    try:
        ssrf.validate_webhook_url(url, resolver=resolver)
        return False
    except ssrf.SSRFError:
        pass
    try:
        ssrf.validate_url(url, resolver, what="Webhook URL", allow=allow)
    except ssrf.SSRFError:
        return False
    log.info("webhook_stored_url_v012: delivering to a stored URL the 0.13 guard refuses")
    return True


def pinned_transport_for(inner: Any = None, resolver: Optional[Resolver] = None,
                         env: Optional[dict] = None) -> Any:
    """The IP-pinned httpx transport per-client deliveries dial through, re-checked at connect with
    the policy's allowance."""
    return ssrf.build_pinned_transport(inner, resolver=resolver, allow=allowance(env))
