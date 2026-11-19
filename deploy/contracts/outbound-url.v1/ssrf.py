"""ssrf.py — the ONE answer to "may this server fetch a URL a user gave it".

Every service that dereferences a user-supplied URL server-side (customer webhooks, calendar feeds,
the agent's web and asset fetches) asks this module, and the module is vendored VERBATIM into each
image that needs it — ``scripts/parity.json`` (fact ``outbound-url-guard``) fails the build when a
copy drifts. Stdlib only; ``httpx`` is imported inside the transport builders, so importing this
module never requires it.

THREE RULES, applied together:

1. **Every address is checked as what it reaches.** An IPv6 address can carry an IPv4 address —
   IPv4-mapped (``::ffff:a.b.c.d``), IPv4-compatible (``::a.b.c.d``), IPv4-translated
   (``::ffff:0:a.b.c.d``), 6to4 (``2002:AABB:CCDD::``), Teredo, NAT64 (``64:ff9b::/96``) and an
   ISATAP interface identifier — and a socket dialled at one of those reaches the IPv4 address
   inside it. ``ip.is_private`` answers for the IPv6 notation, not for that destination, and its
   answer differs between Python patch releases. So the embedded IPv4 address is extracted and
   checked against the IPv4 list, and the IPv6 address is checked against the IPv6 list; either
   being refused refuses the URL. A numeric host that the resolver reads as IPv4 (``2130706433``,
   ``0x7f.1``, ``0177.0.0.1``) is the same address and is read the same way here.
2. **The lists are explicit.** Loopback, private, shared (CGNAT), link-local (cloud metadata),
   unspecified, documentation, benchmarking, reserved, multicast, discard, local-use NAT64, unique
   local and site-local ranges are refused by network, not by ``ipaddress`` flags.
3. **The connection is pinned to the checked address.** Checking a hostname and then letting the
   HTTP client resolve it again is a time-of-check/time-of-use gap: a record flipped between the two
   reaches an internal address. ``validate_url`` resolves the host and checks EVERY address it
   resolves to, returning a ``PinnedURL`` (not a re-resolvable string); the transports re-resolve,
   re-check, and dial the checked address with the original Host header and TLS SNI, on every
   request — redirects included.

A host name is refused before any lookup when it can only name this deployment: ``localhost`` and
anything under ``.localhost``, a cloud metadata name, or a single label (``redis``, ``admin-api`` —
a name with no dot resolves only inside the deployment's own network).

ONE NARROW EXCEPTION, and only an operator can make it: an ``OperatorAllowance`` (parsed from a
deployment env value by ``parse_allowance``, never from anything a user supplies) names internal
hosts or networks that ONE kind of fetch may reach — calendar feeds served inside a corporate
network, for example. A caller passes it explicitly (``allow=``); every other caller is unchanged.
It never admits loopback, link-local (cloud metadata), unspecified, multicast or reserved addresses,
or a deployment-only or metadata host name: ``parse_allowance`` refuses an entry that would, and the
check refuses such an address even when an entry seems to cover it. A listed host name may resolve
to private addresses; any other host is still held to public ones, and the pin and connect-time
re-check apply exactly as before.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any, Callable, List, Optional, Union
from urllib.parse import urlsplit

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
Resolver = Callable[[str], List[str]]

_BLOCKED_IPV4_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8",          # this network; 0.0.0.0 is the unspecified address
    "10.0.0.0/8",         # private
    "100.64.0.0/10",      # shared address space (CGNAT; also a cloud metadata address)
    "127.0.0.0/8",        # loopback
    "169.254.0.0/16",     # link-local, including cloud metadata 169.254.169.254
    "172.16.0.0/12",      # private
    "192.0.0.0/24",       # IETF protocol assignments
    "192.0.2.0/24",       # documentation
    "192.88.99.0/24",     # 6to4 relay anycast
    "192.168.0.0/16",     # private
    "198.18.0.0/15",      # benchmarking
    "198.51.100.0/24",    # documentation
    "203.0.113.0/24",     # documentation
    "224.0.0.0/4",        # multicast
    "240.0.0.0/4",        # reserved, including 255.255.255.255
))

_BLOCKED_IPV6_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "::/128",             # unspecified
    "::1/128",            # loopback
    "64:ff9b:1::/48",     # local-use IPv4/IPv6 translation
    "100::/64",           # discard-only
    "2001::/23",          # IETF protocol assignments, including Teredo 2001::/32
    "2001:db8::/32",      # documentation
    "3fff::/20",          # documentation
    "5f00::/16",          # segment routing SIDs
    "fc00::/7",           # unique local, including cloud metadata fd00:ec2::254
    "fe80::/10",          # link-local
    "fec0::/10",          # site-local
    "ff00::/8",           # multicast
))

#: ``::/8`` holds the mapped, compatible and translated forms (each checked through the IPv4 address
#: it carries). Anything else in it is reserved and refused.
_IPV6_RESERVED_LOW = ipaddress.ip_network("::/8")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_IPV4_TRANSLATED = ipaddress.ip_network("::ffff:0:0:0/96")
_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")
_ISATAP_IDS = (0x00005EFE, 0x01005EFE, 0x02005EFE, 0x03005EFE)

_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "metadata.amazonaws.com",
    "instance-data",
    "instance-data.ec2.internal",
})

#: What a resolver may read as an IPv4 address in the classic ``inet_aton`` forms: up to four
#: decimal, octal or hex parts. Read here so a stub resolver and the real one agree.
_NUMERIC_V4 = re.compile(r"(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+)){0,3}")

REFUSED = "URL cannot target internal or private networks"


class SSRFError(ValueError):
    """A URL this module refuses. The message is safe to show the person who supplied it."""


class PinnedURL:
    """A checked URL and the address(es) it was checked at.

    Deliberately NOT a ``str``: handing a re-resolvable hostname to a transport is the gap rule 3
    closes, so the guard returns this handle and it is not ``==`` to the URL text.

    * ``.url`` — the URL text the transport dials (Host header and TLS SNI are the original host);
    * ``.host`` / ``.port`` / ``.scheme`` — parsed components;
    * ``.pinned_ips`` — every address the host resolved to, each one checked.
    """

    __slots__ = ("url", "host", "port", "scheme", "pinned_ips")

    def __init__(self, url: str, *, host: str, port: Optional[int], scheme: str, pinned_ips: List[str]):
        self.url = url
        self.host = host
        self.port = port
        self.scheme = scheme
        self.pinned_ips = list(pinned_ips)

    def __str__(self) -> str:
        return self.url

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PinnedURL):
            return self.url == other.url and self.pinned_ips == other.pinned_ips
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.url, tuple(self.pinned_ips)))

    def __repr__(self) -> str:
        return f"PinnedURL({self.url!r}, pinned_ips={self.pinned_ips!r})"


def embedded_ipv4(ip: IPAddress) -> List[ipaddress.IPv4Address]:
    """Every IPv4 address ``ip`` carries — what a socket dialled at it would reach. ``[]`` for an
    IPv4 address or an IPv6 address that carries none."""
    if ip.version == 4:
        return []
    out: List[ipaddress.IPv4Address] = []
    low32 = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.ipv4_mapped is not None:
        out.append(ip.ipv4_mapped)
    elif ip in _IPV4_TRANSLATED:
        out.append(low32)
    elif ip in _IPV4_COMPATIBLE and int(ip) > 1:          # not :: and not ::1
        out.append(low32)
    if ip in _NAT64_WELL_KNOWN:
        out.append(low32)
    if ip.sixtofour is not None:
        out.append(ip.sixtofour)
    if ip.teredo is not None:
        out.extend(ip.teredo)                              # (server, client)
    if (int(ip) >> 32) & 0xFFFFFFFF in _ISATAP_IDS:
        out.append(low32)
    return out


def literal_address(text: str) -> Optional[IPAddress]:
    """``text`` as an address, or None — including the numeric forms a resolver reads as IPv4."""
    t = (text or "").strip().strip("[]")
    try:
        return ipaddress.ip_address(t)
    except ValueError:
        pass
    if _NUMERIC_V4.fullmatch(t.lower()):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(t))
        except (OSError, ValueError):
            return None
    return None


def is_blocked_ip(addr: Union[str, IPAddress]) -> bool:
    """True when a socket dialled at ``addr`` would reach an address this module refuses. Anything
    that is not an address is refused: unreadable is not the same as safe."""
    ip = addr if isinstance(addr, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else literal_address(str(addr))
    if ip is None:
        return True
    if ip.version == 4:
        return any(ip in net for net in _BLOCKED_IPV4_NETWORKS)
    carried = embedded_ipv4(ip)
    if any(is_blocked_ip(v4) for v4 in carried):
        return True
    if any(ip in net for net in _BLOCKED_IPV6_NETWORKS):
        return True
    return ip in _IPV6_RESERVED_LOW and not carried


def is_blocked_hostname(hostname: str) -> bool:
    """True for a name that can only name this deployment: ``localhost`` (and ``*.localhost``), a
    cloud metadata name, or a single label."""
    host = (hostname or "").strip().rstrip(".").lower()
    if not host:
        return True
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        return True
    return "." not in host and literal_address(host) is None


def resolve_host(hostname: str) -> List[str]:
    """Every address ``hostname`` resolves to (``[]`` when it does not)."""
    try:
        results = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError, UnicodeError):
        return []
    ips: List[str] = []
    for (_, _, _, _, sockaddr) in results:
        addr = str(sockaddr[0]).split("%", 1)[0]
        if addr and addr not in ips:
            ips.append(addr)
    return ips


#: What no allowance can admit, whatever an operator writes: the addresses that reach this host or
#: its cloud rather than another machine on the corporate network.
_NEVER_ALLOWED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "100.100.100.200/32", "224.0.0.0/4",
    "240.0.0.0/4", "::/128", "::1/128", "fe80::/10", "fd00:ec2::254/128", "ff00::/8",
))

_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def is_never_allowed_ip(addr: Union[str, IPAddress]) -> bool:
    """True for an address no ``OperatorAllowance`` may admit (loopback, link-local and cloud
    metadata, unspecified, multicast, reserved) — read through every IPv4 address it carries."""
    ip = addr if isinstance(addr, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else literal_address(str(addr))
    if ip is None:
        return True
    if ip.version == 6 and any(is_never_allowed_ip(v4) for v4 in embedded_ipv4(ip)):
        return True
    return any(ip.version == net.version and ip in net for net in _NEVER_ALLOWED_NETWORKS)


class OperatorAllowance:
    """Internal hosts and networks an OPERATOR has named for one kind of fetch.

    * ``hosts`` — exact host names (lowercase, no wildcard). A listed name may be a single label and
      may resolve to private addresses; it may never resolve to a never-allowed one.
    * ``networks`` — IP networks whose addresses may be reached, by literal or by a resolved name.

    Built only by ``parse_allowance`` from deployment configuration. An empty allowance admits
    nothing, which is the same as passing none."""

    __slots__ = ("hosts", "networks")

    def __init__(self, hosts=(), networks=()):
        self.hosts = frozenset(h.lower() for h in hosts)
        self.networks = tuple(networks)

    def __bool__(self) -> bool:
        return bool(self.hosts or self.networks)

    def __repr__(self) -> str:
        return f"OperatorAllowance(hosts={sorted(self.hosts)!r}, networks={[str(n) for n in self.networks]!r})"

    def admits_host(self, host: str) -> bool:
        return (host or "").strip().rstrip(".").lower() in self.hosts

    def admits_ip(self, addr: Union[str, IPAddress], *, host: str = "") -> bool:
        """May a connection to ``addr`` (reached through ``host``, when one was named) go ahead?"""
        ip = addr if isinstance(addr, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else literal_address(str(addr))
        if ip is None or is_never_allowed_ip(ip):
            return False
        if host and self.admits_host(host):
            return True
        return any(ip.version == net.version and ip in net for net in self.networks)


def parse_allowance(raw: Optional[str], *, what: str = "allowance") -> OperatorAllowance:
    """Parse an operator's comma- or space-separated list of host names and IP networks (CIDR, or
    a single address) into an ``OperatorAllowance``, or raise ``ValueError`` naming the bad entry.

    Refused: a wildcard, a URL or port, a malformed name, ``localhost``/``*.localhost`` and the cloud
    metadata names, a network with host bits set, and any network that overlaps a never-allowed
    range (so ``0.0.0.0/0``, ``127.0.0.0/8`` and ``169.254.0.0/16`` cannot be listed). ``what``
    names the setting in the message. Empty or ``None`` → an empty allowance."""
    hosts: List[str] = []
    networks: list = []
    for entry in re.split(r"[\s,]+", (raw or "").strip()):
        if not entry:
            continue
        item = entry.lower().rstrip(".")
        if "/" in item or ":" in item or re.fullmatch(r"[0-9.]+", item):
            try:
                net = ipaddress.ip_network(item, strict=True)
            except ValueError:
                raise ValueError(f"{what}: {entry!r} is not an IP network (write it as a CIDR with no "
                                 "host bits, such as 10.20.0.0/16, or a single address)") from None
            if any(net.version == bad.version and net.overlaps(bad) for bad in _NEVER_ALLOWED_NETWORKS):
                raise ValueError(f"{what}: {entry!r} overlaps loopback, link-local (cloud metadata), "
                                 "unspecified, multicast or reserved addresses, which are never allowed")
            networks.append(net)
            continue
        if "*" in item or any(c in item for c in ":/@?#[]"):
            raise ValueError(f"{what}: {entry!r} must be an exact host name or an IP network — no "
                             "wildcard, scheme, port or path")
        if item in _BLOCKED_HOSTNAMES or item.endswith(".localhost") or item == "localhost":
            raise ValueError(f"{what}: {entry!r} names this host or a cloud metadata service and is "
                             "never allowed")
        if len(item) > 253 or not all(_HOST_LABEL.fullmatch(label) for label in item.split(".")):
            raise ValueError(f"{what}: {entry!r} is not a valid host name")
        hosts.append(item)
    return OperatorAllowance(hosts, networks)


def _checked(host: str, resolver: Optional[Resolver], what: str,
             allow: Optional[OperatorAllowance] = None) -> List[str]:
    """The address(es) ``host`` is dialled at, every one checked — or ``SSRFError``. ``allow`` is
    an operator's allowance for this kind of fetch (see ``OperatorAllowance``); ``None`` is the
    rule with no exception."""
    refused = f"{what} cannot target internal or private networks"
    literal = literal_address(host)
    if literal is not None:
        if is_blocked_ip(literal) and not (allow and allow.admits_ip(literal)):
            raise SSRFError(refused)
        return [str(literal)]
    if is_blocked_hostname(host) and not (allow and allow.admits_host(host)):
        raise SSRFError(refused)
    ips = (resolver or resolve_host)(host)
    if not ips:
        raise SSRFError(f"{what} hostname could not be resolved")
    if any(is_blocked_ip(ip) and not (allow and allow.admits_ip(ip, host=host)) for ip in ips):
        raise SSRFError(refused)
    return [str(literal_address(ip)) for ip in ips]


def validate_url(url: str, resolver: Optional[Resolver] = None, *, what: str = "URL",
                 resolve: bool = True, allow: Optional[OperatorAllowance] = None) -> PinnedURL:
    """Check ``url`` and return a ``PinnedURL``, or raise ``SSRFError``.

    http(s) only, a host, and every address the host reaches checked (rule 1). ``resolver`` replaces
    DNS (tests); ``resolve=False`` checks the URL as written — scheme, literal address, host name —
    without a lookup, for a WRITE that only stores the URL (the fetch that follows resolves and
    pins). ``what`` names the URL in the message the person sees. ``allow`` is an operator's
    allowance for this kind of fetch, never a user's (see ``OperatorAllowance``)."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        raise SSRFError(f"{what} is not a valid URL") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise SSRFError(f"{what} must use http or https scheme")
    host = (parts.hostname or "").lower()
    if not host:
        raise SSRFError(f"{what} must have a valid hostname")
    if resolve:
        ips = _checked(host, resolver, what, allow)
    else:
        literal = literal_address(host)
        if literal is not None:
            refused = is_blocked_ip(literal) and not (allow and allow.admits_ip(literal))
        else:
            refused = is_blocked_hostname(host) and not (allow and allow.admits_host(host))
        if refused:
            raise SSRFError(f"{what} cannot target internal or private networks")
        ips = [str(literal)] if literal is not None else []
    return PinnedURL(url, host=host, port=port, scheme=parts.scheme.lower(), pinned_ips=ips)


def validate_webhook_url(url: str, resolver: Optional[Resolver] = None) -> PinnedURL:
    """``validate_url`` for a customer webhook destination."""
    return validate_url(url, resolver, what="Webhook URL")


def revalidate_at_connect(hostname: str, resolver: Optional[Resolver] = None,
                          allow: Optional[OperatorAllowance] = None) -> List[str]:
    """Re-resolve and re-check ``hostname`` at the moment of dialling; the checked address(es)."""
    return _checked((hostname or "").lower(), resolver, "URL", allow)


def _pin(request: Any, resolver: Optional[Resolver],
         allow: Optional[OperatorAllowance] = None) -> None:
    """Point ``request`` at a checked address, keeping the original Host header and TLS SNI."""
    host = request.url.host
    ips = revalidate_at_connect(host, resolver, allow)
    if literal_address(host) is not None:
        return                                   # a literal address, already checked: dial as is
    request.headers.setdefault("Host", request.url.netloc.decode("ascii"))
    request.extensions = dict(request.extensions)
    request.extensions["sni_hostname"] = host
    request.url = request.url.copy_with(host=ips[0])


def build_pinned_transport(inner: Any = None, resolver: Optional[Resolver] = None,
                           allow: Optional[OperatorAllowance] = None) -> Any:
    """An ``httpx.AsyncBaseTransport`` that checks and pins every request it sends (rule 3).
    ``allow`` is an operator's allowance for the one kind of fetch this transport serves."""
    import httpx

    base = inner if inner is not None else httpx.AsyncHTTPTransport()

    class _PinnedTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: "httpx.Request") -> "httpx.Response":
            _pin(request, resolver, allow)
            return await base.handle_async_request(request)

        async def aclose(self) -> None:
            await base.aclose()

    return _PinnedTransport()


def build_pinned_sync_transport(inner: Any = None, resolver: Optional[Resolver] = None,
                                allow: Optional[OperatorAllowance] = None) -> Any:
    """The same, as an ``httpx.BaseTransport`` for a synchronous client."""
    import httpx

    base = inner if inner is not None else httpx.HTTPTransport()

    class _PinnedSyncTransport(httpx.BaseTransport):
        def handle_request(self, request: "httpx.Request") -> "httpx.Response":
            _pin(request, resolver, allow)
            return base.handle_request(request)

        def close(self) -> None:
            base.close()

    return _PinnedSyncTransport()
