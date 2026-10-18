"""Is this the address a person outside the deployment can open? One answer, two readers.

The rig hands people links built from its published MCP address (`VEXA_PUBLIC_MCP_URL`): the
sign-in page `auth_link` returns, the `persist_now` command and skill URL `auth_claim` and
`confirm_login` return. A link built from a listen address (a Docker bridge IP, a container name,
`0.0.0.0`) opens nowhere for that person, and the agent has no way to tell.

Two readers, one rule, so they cannot drift:

* `deploy/dogfood/minutes-stack/agent_mcp.py` refuses to boot on an unusable `public_url`
  (P18, fail loud) with ``allow_loopback=False``: a deployment is never published on loopback.
* `vexa_control_mcp.py` checks again before any sign-in verb hands out a link (defence in depth)
  with ``allow_loopback=True``: a rig run on a laptop serves a client on that same laptop, and a
  loopback link works there.

Stdlib only: the rig and the composition root both load this file by path.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

#: Name suffixes that only resolve inside a private network.
PRIVATE_SUFFIXES = (".local", ".internal", ".lan", ".home.arpa", ".localdomain")
LOOPBACK_NAMES = ("localhost",)


def _is_loopback_name(host: str) -> bool:
    return host in LOOPBACK_NAMES or host.endswith(".localhost")


def problem(url: str, *, allow_loopback: bool = False) -> str:
    """Why ``url`` cannot be the public MCP address, or ``""`` when it can.

    Required: an absolute ``https`` URL whose path ends in ``/mcp`` and whose host is a public
    name or a public IP. Refused: private, loopback, link-local, unspecified, reserved and
    multicast IPs; single-label names (a container or service name) and private-network
    suffixes. ``allow_loopback`` admits ``localhost``/``127.0.0.1``/``::1`` over http or https,
    and nothing else."""
    raw = (url or "").strip()
    if not raw:
        return "no public address is configured"
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower().rstrip(".")
        parts.port  # noqa: B018 — raises on a malformed port
    except ValueError as e:
        return f"{raw!r} is not a URL ({e})"
    if not host:
        return f"{raw!r} names no host"
    if not parts.path.rstrip("/").endswith("/mcp"):
        return f"{raw!r} must end in /mcp (the MCP endpoint every link is built beside)"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    loopback = (ip.is_loopback if ip else _is_loopback_name(host))
    if loopback:
        if allow_loopback and parts.scheme in ("http", "https"):
            return ""
        return f"{host} is a loopback address; nobody outside this machine can open it"
    if ip is not None:
        if (ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_reserved
                or ip.is_multicast):
            return f"{host} is a private or non-routable address; a person cannot open it"
    else:
        if "." not in host:
            return f"{host} is a single-label name (a container or service name), not a public one"
        if host.endswith(PRIVATE_SUFFIXES):
            return f"{host} only resolves inside a private network"
    if parts.scheme != "https":
        return f"{raw!r} must be https; a sign-in link carries a one-time code"
    return ""
