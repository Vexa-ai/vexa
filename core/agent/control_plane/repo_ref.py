"""repo_ref.py — what a caller-supplied "Repository" may be, decided BEFORE any git process starts.

``POST /api/workspace/swap`` and ``POST /api/workspace/activate`` take a repository from the caller
and hand it to ``git clone``. ``git clone`` is a fetch **this server** performs, so the field is not
only a name for something the caller owns — it is an instruction to this process to go somewhere and
come back with the result. Two properties follow, and neither was true before this module existed:

* **the HOST must be somewhere the caller could have reached themselves.** ``http://169.254.169.254/a/b``
  is the cloud metadata service and ``http://admin-api:8001/a/b`` is a compose neighbour; both are
  perfectly well-formed repository URLs, and the outcome of the fetch comes back to the caller in the
  (token-redacted) clone error. ``assert_public_host`` is that half.
* **the TRANSPORT must be one we named.** ``ext::sh -c <command>`` is a git URL that runs a shell
  command, and ``file:///etc`` is a git URL that reads the local filesystem. ``assert_allowed_scheme``
  is that half, and ``shared.gitenv.pinned_git_env`` enforces the same list a second time inside git
  itself, so a transport we did not name cannot be reached even by a caller we did not anticipate.

Both checks run **before any subprocess is created**, which is the part that matters: a validator that
runs after git has already been told where to go has not protected anything.

The checks are duplicated — once at the route, once inside ``workspace_attach._git_clone`` — on
purpose, so a credential-bearing fetch cannot reach git through the MCP, a future route, or a test
that forgot.

**What this module does NOT close, stated plainly:** a DNS name that resolves to an internal address
(``localtest.me``, a rebinding record) passes ``_host_is_internal``, because the check is on the name
and the resolution happens inside git. Closing that needs resolution-time enforcement (resolve, check
every answer, pin the connection), which is a different mechanism than a string check.
"""
from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Optional


class RepoRefError(ValueError):
    """A repository reference we refuse. ``kind`` names which gate refused it (``host`` · ``scheme`` ·
    ``local``) so a caller can branch without parsing the message."""

    def __init__(self, message: str, *, kind: str = "shape") -> None:
        super().__init__(message)
        self.kind = kind


#: What the user is told when the shape is right but the HOST is somewhere only this server can reach.
HOST_SENTENCE = ("That host is not reachable as a repository. Use a public or company git host, "
                 "not an address inside the deployment.")
#: …and when the TRANSPORT is one we do not carry.
SCHEME_SENTENCE = ("That is not a repository URL we can fetch. Use https://, ssh://, or "
                   "git@host:owner/repo.")
#: …and when it is a path on this server's own disk, which is not a thing a caller may name.
LOCAL_SENTENCE = ("That is a path on the server, not a repository URL. Use https://, ssh://, or "
                  "git@host:owner/repo.")

#: The transports a caller-supplied repository may name. Everything else — ``ext``, ``file``, ``git``,
#: ``ftp`` and the whole ``<helper>::`` family — is refused: ``ext::`` runs a shell command and
#: ``file://`` reads this server's disk, and neither is a fetch the caller could have made themselves.
ALLOWED_SCHEMES = ("https", "http", "ssh")

#: Opt-in escape hatch for a SELF-HOSTED deployment that legitimately attaches repos from a local path
#: or an NFS mount: an ``os.pathsep``-separated list of roots a scheme-less path may live under. UNSET
#: (the default, and every hosted deployment) means a local path is refused outright — which is the
#: behaviour change this module introduces for self-hosters, and the reason it is an env var rather
#: than a removal: ``git clone /var/lib/vexa/workspaces/<someone-else>`` was a read of another
#: subject's workspace, and a deployment that really does clone from disk must now say so.
LOCAL_ROOTS_ENV = "VEXA_ALLOW_LOCAL_REPO_ROOT"

#: ``scheme://…`` — a transport named the ordinary way.
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")
#: ``helper::…`` — git's remote-helper syntax, which is how ``ext::sh -c …`` gets to run a command.
_HELPER = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)::")
#: ``user@host:path`` — the scp-like form, the one shape with a host and no scheme.
_SCP = re.compile(r"^[A-Za-z0-9._-]+@(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+):")
#: The host of a URL, userinfo skipped (``https://token@host/…`` must be read as ``host``).
_URL_HOST = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^/@\s]+@)?([^/\s]+)")


def _bare_host(host: str) -> str:
    """``host[:port]`` → ``host``, brackets stripped, lowercased. A bracketed literal (``[::1]:8080``)
    is unwrapped first, because an unbracketed IPv6 address is all colons and a naive port strip
    would turn ``::1`` into ``:``."""
    h = (host or "").strip().rstrip(".").lower()
    if h.startswith("["):
        end = h.find("]")
        return h[1:end] if end > 0 else h[1:]
    if h.count(":") == 1 and re.search(r":\d{1,5}$", h):
        h = h.rsplit(":", 1)[0]
    return h


def _unwrapped(ip: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    """The IPv4 address an IPv6 address is carrying, if it is carrying one.

    ``::ffff:127.0.0.1`` is loopback written as IPv6, and ``IPv6Address.is_loopback`` is False for it —
    the flag describes ``::1``, not what the address maps to. Same for 6to4 and Teredo. Unwrap first,
    then ask the question, so the answer does not depend on which notation the caller chose."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return mapped
    sixtofour = getattr(ip, "sixtofour", None)
    if sixtofour is not None:
        return sixtofour
    teredo = getattr(ip, "teredo", None)
    if teredo:
        return teredo[1]
    return ip


def _host_is_internal(host: str) -> bool:
    """Is this host somewhere only the SERVER can reach — i.e. would fetching it be a request the
    caller could not have made themselves?

    Two rules, and the second is the one that catches a service name:

    * a literal IP (in any notation) that is loopback, private, link-local, reserved, multicast or
      unspecified — ``127.0.0.1``, ``10.0.0.5``, ``169.254.169.254``, ``::1``, ``::ffff:127.0.0.1``;
    * a BARE LABEL — a name with no dot. Every public and company git host is fully qualified; an
      unqualified name resolves only inside the deployment's own network, which is the whole class
      ``admin-api``, ``redis`` and ``meeting-api`` belong to. ``localhost`` included.

    A DOTTED name is left alone even when it is obviously internal (``git.internal:8080`` is an
    accepted self-hosted mirror): a name with a dot is one somebody configured, and refusing it would
    break the self-host case to close nothing the two rules above leave open — the deployment's own
    neighbours all answer to bare labels."""
    h = _bare_host(host)
    if not h:
        return True
    try:
        ip = _unwrapped(ipaddress.ip_address(h))
    except ValueError:
        return "." not in h               # a bare label: only resolvable inside the deployment
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)


def _checked_host(host: str) -> str:
    """``host``, or ``RepoRefError`` — the seam a URL normalizer threads every parsed host through."""
    if _host_is_internal(host):
        raise RepoRefError(HOST_SENTENCE, kind="host")
    return host


def host_of(raw: Optional[str]) -> Optional[str]:
    """The host a repository reference names, or ``None`` when it names none (a local path)."""
    v = (raw or "").strip()
    m = _URL_HOST.match(v) or _SCP.match(v)
    return m.group(1) if m else None


def assert_public_host(raw: Optional[str]) -> None:
    """Refuse a deployment-internal HOST, wherever the call came from.

    Deliberately NARROWER than :func:`assert_allowed_scheme`, so it can also sit inside
    ``workspace_attach._git_clone`` — which is reached by tests and internal callers with values that
    name no host at all. It says nothing about a value that is not URL-shaped; only that a value which
    IS one may not point at something only this server can reach."""
    host = host_of(raw)
    if host is not None and _host_is_internal(host):
        raise RepoRefError(HOST_SENTENCE, kind="host")


def allowed_local_roots() -> list[Path]:
    """The roots a scheme-less repository path may live under — empty unless a self-host operator set
    :data:`LOCAL_ROOTS_ENV`, which is the default and means "no local paths"."""
    raw = os.environ.get(LOCAL_ROOTS_ENV, "")
    roots: list[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        try:
            roots.append(Path(part).resolve())
        except OSError:                     # an unresolvable root configures nothing
            continue
    return roots


def contained_local_path(value: str) -> Path:
    """The RESOLVED path a scheme-less repository reference names, proven to sit under a configured
    root — or ``RepoRefError``. The caller-supplied string never leaves this function.

    Containment is asserted TWICE, and the order is the point:

    * **lexically first**, on ``os.path.normpath(os.path.abspath(value))``. That collapses ``..``
      without touching the filesystem, so a value that escapes a root is refused *before* it is used
      in any path expression at all;
    * **then again on the symlink-resolved path**, because a symlink planted inside a root points
      wherever it likes and the lexical pass cannot see through it.

    Only the resolved path is returned, so no caller can reach past the check by re-using the raw
    string."""
    roots = allowed_local_roots()
    if not roots:
        raise RepoRefError(LOCAL_SENTENCE, kind="local")

    prefixes = [os.path.normpath(os.path.abspath(str(r))) for r in roots]
    candidate = os.path.normpath(os.path.abspath(value.strip()))
    if not any(candidate == p or candidate.startswith(p + os.sep) for p in prefixes):
        raise RepoRefError(LOCAL_SENTENCE, kind="local")

    try:
        resolved = Path(candidate).resolve(strict=False)
        real_roots = [Path(p).resolve(strict=False) for p in prefixes]
    except OSError:
        raise RepoRefError(LOCAL_SENTENCE, kind="local") from None
    if not any(resolved == r or resolved.is_relative_to(r) for r in real_roots):
        raise RepoRefError(LOCAL_SENTENCE, kind="local")
    return resolved


def assert_allowed_scheme(raw: Optional[str]) -> None:
    """Refuse a transport we did not name — the caller-supplied half of the gate.

    ``ext::sh -c …`` runs a command, ``file:///etc`` reads this server's disk, ``git://`` is
    unauthenticated and unencrypted, and a scheme-less path is a location on this server rather than a
    repository anyone could name. Each is a well-formed git URL, which is exactly why a shape check
    that only asks "does this parse" does not catch them."""
    v = (raw or "").strip()
    if not v:
        return                              # omitted → swap back to the seed; never reaches git
    if _HELPER.match(v):                    # ext:: / any remote-helper transport
        raise RepoRefError(SCHEME_SENTENCE, kind="scheme")
    m = _SCHEME.match(v)
    if m:
        if m.group(1).lower() not in ALLOWED_SCHEMES:
            raise RepoRefError(SCHEME_SENTENCE, kind="scheme")
        return
    if _SCP.match(v):
        return
    contained_local_path(v)                 # refuses unless it resolves inside a configured root


def assert_fetchable(raw: Optional[str]) -> None:
    """The whole gate for a caller-supplied repository: a transport we carry, at a host the caller
    could have reached. What every API route calls before the value goes anywhere near git."""
    assert_allowed_scheme(raw)
    assert_public_host(raw)
