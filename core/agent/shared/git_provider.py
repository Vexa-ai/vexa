"""Which git provider a host runs, and how a token is presented to it.

A token reaches git as the USERINFO of an https URL (``shared.token_destination.embed_token``), and
providers disagree about the shape of that userinfo:

* **GitHub** accepts the token on its own: ``https://<token>@github.com/owner/repo``.
* **GitLab** wants a username and the token as the password: ``https://oauth2:<token>@host/group/repo``.
  ``oauth2`` works for personal, project, group and OAuth access tokens alike.
* **Other servers** (Gitea, Bitbucket Server, a generic HTTP git server) want a named account and the
  token as its password: ``https://<user>:<token>@host/...``.

Two ways to say which applies, and the first one a person controls:

1. **The token itself.** A per-call token written ``<user>:<token>`` is used exactly as written — the
   person named the account. ``oauth2:<token>`` is the common GitLab case.
2. **The operator's host map**, ``VEXA_GIT_PROVIDERS``: comma-separated ``<host>=<provider>`` entries,
   where provider is ``github``, ``gitlab``, or ``basic:<username>``. A host that is not listed is
   treated as ``github``, which is how every deployment behaved before the map existed.

The map is parsed by :func:`parse_providers`, which raises on any entry it cannot read; agent-api runs it
at boot (``shared.config.Settings``), so a typo stops the service instead of silently sending a token
in the wrong shape. Nothing here logs, returns or stores a token: :func:`userinfo` builds the string for
one network op and the caller passes it to git and drops it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlsplit

PROVIDERS_ENV = "VEXA_GIT_PROVIDERS"

GITHUB = "github"
GITLAB = "gitlab"
BASIC = "basic"
KINDS = (GITHUB, GITLAB, BASIC)

#: The username GitLab documents for token-over-https.
GITLAB_USERNAME = "oauth2"

_HOSTNAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_USERNAME = re.compile(r"^[A-Za-z0-9._@+-]{1,64}$")


@dataclass(frozen=True)
class Provider:
    """What a host runs. ``username`` is set for ``basic`` only (``gitlab`` always uses ``oauth2``)."""

    kind: str
    username: Optional[str] = None


DEFAULT = Provider(GITHUB)


def parse_providers(raw: Optional[str]) -> dict[str, Provider]:
    """``VEXA_GIT_PROVIDERS`` → ``{hostname: Provider}``. Raises ``ValueError`` naming the bad entry
    (never a secret: the value is host names and provider words)."""
    out: dict[str, Provider] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        host, sep, spec = entry.partition("=")
        host, spec = host.strip().lower().rstrip("."), spec.strip()
        if not sep or not _HOSTNAME.match(host) or "." not in host:
            raise ValueError(f"{PROVIDERS_ENV}: '{entry}' is not <host>=<provider> with a dotted host name")
        kind, _, user = spec.partition(":")
        kind = kind.strip().lower()
        if kind not in KINDS:
            raise ValueError(f"{PROVIDERS_ENV}: '{entry}' names provider '{kind}'; use github, gitlab or basic:<username>")
        if kind == BASIC:
            user = user.strip()
            if not _USERNAME.match(user):
                raise ValueError(f"{PROVIDERS_ENV}: '{entry}' needs basic:<username> (letters, digits, . _ @ + -)")
            provider = Provider(BASIC, user)
        else:
            if user:
                raise ValueError(f"{PROVIDERS_ENV}: '{entry}' — only basic takes a username")
            provider = Provider(kind)
        if host in out and out[host] != provider:
            raise ValueError(f"{PROVIDERS_ENV}: host '{host}' is listed twice with different providers")
        out[host] = provider
    return out


def providers() -> dict[str, Provider]:
    """The deployment's host map, read from the environment on every call (boot already validated it)."""
    return parse_providers(os.environ.get(PROVIDERS_ENV, ""))


def hostname_of(url_or_host: str) -> str:
    """The lowercased host name of a URL, an scp-like ``user@host:path`` reference, or a bare host."""
    v = (url_or_host or "").strip()
    if "://" in v:
        try:
            return (urlsplit(v).hostname or "").lower().rstrip(".")
        except ValueError:
            return ""
    m = re.match(r"^[A-Za-z0-9._-]+@([A-Za-z0-9.-]+):", v)
    if m:
        return m.group(1).lower().rstrip(".")
    return v.split("/", 1)[0].rsplit(":", 1)[0].lower().rstrip(".") if v else ""


def provider_for(url_or_host: str) -> Provider:
    """The provider the host of ``url_or_host`` runs: ``github.com`` is GitHub, a mapped host is what
    the operator said, anything else is :data:`DEFAULT`."""
    host = hostname_of(url_or_host)
    if host == "github.com":
        return Provider(GITHUB)
    return providers().get(host, DEFAULT)


def split_token(token: Optional[str]) -> tuple[Optional[str], str]:
    """``"<user>:<secret>"`` → ``(user, secret)``; anything else → ``(None, token)``. The secret is
    what an API call authenticates with; the user only ever appears in a URL's userinfo."""
    t = (token or "").strip()
    user, sep, secret = t.partition(":")
    if sep and user and secret and _USERNAME.match(user):
        return user, secret
    return None, t


def secret_of(token: Optional[str]) -> str:
    """The password half of a token — what a redactor must also remove."""
    return split_token(token)[1]


def userinfo(url: str, token: str) -> str:
    """The URL userinfo that presents ``token`` to the host of ``url``, percent-encoded."""
    user, secret = split_token(token)
    if user is None:
        provider = provider_for(url)
        if provider.kind == GITLAB:
            user = GITLAB_USERNAME
        elif provider.kind == BASIC:
            user = provider.username
    if user is None:
        return quote(secret, safe="")
    return f"{quote(user, safe='')}:{quote(secret, safe='')}"
