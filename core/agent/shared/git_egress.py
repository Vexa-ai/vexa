"""git_egress.py — where a caller-supplied git remote may send THIS server, and the pin that holds it there.

``git clone``/``fetch``/``push`` to a URL a person typed is a request this server makes. Two questions
decide whether it may, and both are answered here so the route gate (``control_plane.repo_ref``) and
the connection itself (``shared.gitenv.git_transport``) cannot disagree:

1. **Where does the host lead?** A literal address is read as what it reaches (``shared.ssrf``); a
   bare label (``admin-api``, ``redis``) only resolves inside the deployment; a DOTTED name is
   resolved, and every address it resolves to is checked. Any loopback, private, link-local (cloud
   metadata), CGNAT or reserved answer refuses the host — ``git.corp.example`` pointing at
   ``10.0.0.7`` is a deployment-internal fetch whatever its name says.
2. **Did the operator name it?** ``VEXA_GIT_ALLOWED_INTERNAL_HOSTS`` is the operator's list of
   internal git hosts (exact names) and networks (CIDR) a remote may reach — the self-hosted mirror
   case. It is an ``shared.ssrf.OperatorAllowance``: it never admits loopback, link-local/metadata,
   unspecified, multicast or reserved addresses, whatever it says. A host the operator configured as
   a git provider (``VEXA_GIT_PROVIDERS``) is the operator's as well, and is not checked or pinned.

**The connection is pinned to the checked addresses.** Resolving a name, checking it, and then letting
git resolve it again is a time-of-check/time-of-use gap a rebinding record walks through. So the
transport resolves and checks once more immediately before git starts, and hands git exactly those
addresses: ``http.curloptResolve`` for http(s) (curl dials the pinned address and keeps the name for
the Host header, TLS SNI and certificate check), and ``ssh -o HostName=<address> -o
HostKeyAlias=<name>`` for ssh (ssh dials the address and checks the host key under the name).
Redirects are already off (``shared.gitexec``: ``http.followRedirects=false``), so the pinned host is
the only one git talks to. An outbound proxy (``HTTPS_PROXY``) resolves names itself; behind one, the
proxy's own egress policy is what holds.

The route gate lets an UNRESOLVABLE name through (there is nothing to fetch; git would fail), the
connection does not: no answer at connect time is a refusal, so a record that appears between the
check and the clone is never followed.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
from typing import Callable, List, NamedTuple, Optional

from shared import git_provider, ssrf

log = logging.getLogger("git_egress")

#: The operator's allow-list of internal git hosts and networks (comma or space separated: exact host
#: names and CIDRs). Unset — the default — admits none.
ALLOWED_INTERNAL_HOSTS_ENV = "VEXA_GIT_ALLOWED_INTERNAL_HOSTS"

#: DNS for every check in this module. Tests replace it; nothing else should.
resolver: Callable[[str], List[str]] = ssrf.resolve_host


class GitEgressRefused(ValueError):
    """A git remote this server may not reach. The message is safe to show the person who named it."""


_DEFAULT_PORTS = {"https": 443, "http": 80, "ssh": 22}
_URL = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://(?:[^/@\s]+@)?(\[[0-9A-Fa-f:.]+\]|[^/:\s]+)(?::(\d{1,5}))?")
_SCP = re.compile(r"^[A-Za-z0-9._-]+@(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+):")


class Remote(NamedTuple):
    scheme: str     # https · http · ssh
    host: str       # lowercased, brackets stripped
    port: int


def remote_of(url: Optional[str]) -> Optional[Remote]:
    """The network endpoint a git URL names, or ``None`` for one that names none (a local path, or a
    transport nothing here carries — those are refused elsewhere, by name)."""
    v = (url or "").strip()
    m = _URL.match(v)
    if m:
        scheme = m.group(1).lower()
        if scheme not in _DEFAULT_PORTS:
            return None
        host = m.group(2).strip("[]").rstrip(".").lower()
        return Remote(scheme, host, int(m.group(3)) if m.group(3) else _DEFAULT_PORTS[scheme])
    m = _SCP.match(v)
    if m:
        return Remote("ssh", m.group(1).strip("[]").rstrip(".").lower(), 22)
    return None


def allowance() -> ssrf.OperatorAllowance:
    """The operator's allowance, read at call time. A malformed value is a ``GitEgressRefused`` (and
    a log line naming the setting): it admits nothing rather than guessing what was meant."""
    raw = os.environ.get(ALLOWED_INTERNAL_HOSTS_ENV, "")
    try:
        return ssrf.parse_allowance(raw, what=ALLOWED_INTERNAL_HOSTS_ENV)
    except ValueError as exc:
        log.error("%s", exc)
        raise GitEgressRefused(f"{ALLOWED_INTERNAL_HOSTS_ENV} is malformed; no internal git host "
                               "is allowed until it is fixed") from None


def is_operator_provider(host: str) -> bool:
    h = (host or "").strip().rstrip(".").lower()
    return any(p.host == h for p in git_provider.registry().all())


_REFUSED = "Repository host cannot be an address inside the deployment"


def checked_addresses(host: str, *, at_connect: bool) -> Optional[List[str]]:
    """The addresses a git remote on ``host`` may be dialled at, every one checked — or
    ``GitEgressRefused``.

    ``None`` means "no pin applies": the host is an operator-configured git provider, or (only when
    ``at_connect`` is false) the name does not resolve. At connect time an unresolvable name is a
    refusal, because the pin has nothing to hold."""
    h = (host or "").strip().strip("[]").rstrip(".").lower()
    if not h:
        raise GitEgressRefused(_REFUSED)
    if is_operator_provider(h):
        return None
    allow = allowance()
    literal = ssrf.literal_address(h)
    if literal is not None:
        if ssrf.is_blocked_ip(literal) and not allow.admits_ip(literal):
            raise GitEgressRefused(_REFUSED)
        return [str(literal)]
    if ssrf.is_blocked_hostname(h) and not allow.admits_host(h):
        raise GitEgressRefused(_REFUSED)
    ips = resolver(h)
    if not ips:
        if at_connect:
            raise GitEgressRefused("Repository host could not be resolved")
        return None
    try:
        return ssrf.revalidate_at_connect(h, lambda _name: ips, allow)
    except ssrf.SSRFError:
        raise GitEgressRefused(_REFUSED) from None


def pin_config(remote: Remote, ips: List[str]) -> tuple[str, ...]:
    """``-c`` pairs that make git's curl dial ``ips`` for ``remote`` (http/https only)."""
    addrs = ",".join(f"[{ip}]" if ":" in ip else ip for ip in ips)
    return ("-c", f"http.curloptResolve={remote.host}:{remote.port}:{addrs}")


def pin_ssh_command(remote: Remote, ips: List[str], base: Optional[str]) -> str:
    """``base`` (the ssh command the op would have run — a deploy key's, or plain ``ssh``, which is
    what ``shared.gitexec`` pins) told to dial the first checked address and to check the host key
    under the name the person gave."""
    alias = remote.host if remote.port == 22 else f"[{remote.host}]:{remote.port}"
    cmd = (base or "").strip() or "ssh"
    return (f"{cmd} -o HostName={shlex.quote(ips[0])} -o HostKeyAlias={shlex.quote(alias)} "
            "-o CheckHostIP=no")
