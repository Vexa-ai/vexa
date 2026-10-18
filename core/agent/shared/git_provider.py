"""The git provider port: every question agent-api asks a git HOST goes through one ``GitProvider``.

A workspace's repository lives on a git host, and hosts differ in ways this module owns:

* **where a repository path lives** — ``owner/repo`` on GitHub; ``group/sub/…/project`` on GitLab,
  under a base URL that may carry a port and a path prefix (``https://git.example.com:8443/gitlab``);
* **how a token is presented to git** — GitHub takes the token as the whole userinfo, GitLab wants
  ``oauth2:<token>``, other servers want ``<user>:<token>``. A per-call token written
  ``<user>:<token>`` is used exactly as written — the person named the account;
* **how a repository is created** under an owner, org or (nested) group, through the host's API;
* **how a token is checked** (whose account it is);
* **where a person adds a deploy key**, and **what the web link of a repository is**.

The port is :class:`GitProvider`; the adapters are ``shared.git_provider_github.GitHubProvider`` and
``shared.git_provider_gitlab.GitLabProvider``. Nothing outside the GitHub adapter and its defaults
names GitHub's host (``tests/test_git_provider_port.py`` holds that statically).

**Which providers exist is the operator's configuration, ``VEXA_GIT_PROVIDERS``** (Helm
``agentApi.gitProviders``). Two spellings are read:

* a JSON list — ``[{"name": "corp", "kind": "gitlab", "baseUrl": "https://git.corp.example",
  "caBundle": "-----BEGIN CERTIFICATE-----…"}]``. ``kind`` is ``github`` or ``gitlab``; ``caBundle``
  (PEM) or ``caFile`` (a mounted path) is the private CA that host's certificate is issued by;
  ``label`` is the name people see; ``apiUrl`` overrides the API root (GitHub Enterprise);
* the earlier comma list — ``gitlab.example.com=gitlab,git.example.org=basic:svc`` — where ``basic``
  only shapes the token and creates nothing.

The built-in GitHub provider (name ``github``) is always present, so a deployment that configured
nothing behaves exactly as before. The configuration is parsed at boot (``shared.config.Settings``):
a malformed entry stops agent-api instead of silently sending a token in the wrong shape.

Egress: a provider's API is called only at the root the operator configured (or the built-in GitHub
API), never at a host a request names — a request may only PICK a configured provider by name. Every
API call refuses redirects, so a token in a header is never handed on. Nothing here logs, returns or
stores a token.
"""
from __future__ import annotations

import abc
import hashlib
import json
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote, urlsplit

PROVIDERS_ENV = "VEXA_GIT_PROVIDERS"

GITHUB = "github"
GITLAB = "gitlab"
BASIC = "basic"
KINDS = (GITHUB, GITLAB, BASIC)
#: The kinds an operator may list as a full provider (``basic`` is a token shape only).
PROVIDER_KINDS = (GITHUB, GITLAB)

#: The username GitLab documents for token-over-https.
GITLAB_USERNAME = "oauth2"

#: The name of the provider every deployment has.
DEFAULT_NAME = GITHUB

_HOSTNAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_USERNAME = re.compile(r"^[A-Za-z0-9._@+-]{1,64}$")
_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
_SEG = re.compile(r"^[A-Za-z0-9._-]+$")

#: The deepest repository path any provider accepts: GitLab's group · 20 subgroups · project.
MAX_PATH_SEGMENTS = 22


# ── typed faults ──────────────────────────────────────────────────────────────────────────────────

class GitProviderError(RuntimeError):
    """A provider call failed. ``fault`` names what, so a route can answer with a typed refusal:
    ``token_rejected`` · ``repo_exists`` · ``namespace_not_found`` · ``unreachable`` · ``redirect`` ·
    ``failed`` · ``unsupported``. The message is token-free and names the provider."""

    def __init__(self, message: str, *, fault: str = "failed", provider: str = ""):
        super().__init__(message)
        self.fault = fault
        self.provider = provider


def redact(text: str, token: Optional[str]) -> str:
    """``text`` with every spelling of ``token`` (as given, and its password half) removed."""
    for k in sorted({(token or "").strip(), secret_of(token)} - {""}, key=len, reverse=True):
        text = text.replace(k, "***")
    return text


# ── the port ──────────────────────────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is refused, not followed: the request carries the caller's token in a header, and
    following a redirect would hand it to whatever host the answer names."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


class GitProvider(abc.ABC):
    """One configured git host. The URL half is shared here; adapters implement the API half."""

    kind: str = ""
    #: The deepest repository path the host accepts (``owner/repo`` is 2).
    max_path_segments: int = 2
    #: Whether an owner may be a nested group path (``group/sub``).
    nested_groups: bool = False
    #: Whether this deployment may create repositories here.
    can_create: bool = True
    #: The username that goes before the token in a URL's userinfo; ``None`` = the token alone.
    token_user: Optional[str] = None

    def __init__(self, name: str, base_url: str, *, label: Optional[str] = None,
                 ca_file: Optional[str] = None, api_url: Optional[str] = None, opener: Any = None):
        try:
            parts = urlsplit((base_url or "").strip())
            port = parts.port
        except ValueError:
            parts, port = None, None
        if parts is None or parts.scheme.lower() != "https" or not parts.hostname or parts.username \
                or parts.password or parts.query or parts.fragment:
            raise ValueError(f"{PROVIDERS_ENV}: provider '{name}' needs an https:// baseUrl with a host "
                             f"and nothing else (no user, query or fragment)")
        self.name = name
        self.label = label or name
        self.host = parts.hostname.lower().rstrip(".")
        self.port = port if port and port != 443 else None
        self.prefix = parts.path.rstrip("/")
        self.base_url = f"https://{self.netloc()}{self.prefix}"
        self.ca_file = ca_file
        self._api_url = api_url.rstrip("/") if api_url else None
        self._opener = opener

    # -- URL half ----------------------------------------------------------------------------------
    def netloc(self) -> str:
        return self.host + (f":{self.port}" if self.port else "")

    @property
    def api_url(self) -> str:
        return self._api_url or self.default_api_url()

    @abc.abstractmethod
    def default_api_url(self) -> str:
        """The API root when the operator did not name one."""

    def matches(self, url_or_host: str) -> bool:
        """Whether ``url_or_host`` (an http(s)/ssh URL, an scp-like ref, or a bare host) is on this
        provider. http(s) compares host, port and path prefix; ssh compares the host."""
        v = (url_or_host or "").strip()
        if "://" in v:
            try:
                p = urlsplit(v)
                host, port = (p.hostname or "").lower().rstrip("."), p.port
            except ValueError:
                return False
            if host != self.host:
                return False
            if p.scheme.lower() in ("https", "http"):
                if p.scheme.lower() == "https" and (port or 443) != (self.port or 443):
                    return False
                if p.scheme.lower() == "http" and (port or None) != self.port:
                    return False
                return not self.prefix or p.path == self.prefix or p.path.startswith(self.prefix + "/")
            return True
        return hostname_of(v) == self.host

    def repo_path(self, url: str) -> Optional[str]:
        """``group/sub/repo`` from a URL on this provider (``.git`` dropped), or ``None``."""
        if not self.matches(url):
            return None
        v = (url or "").strip()
        if "://" in v:
            p = urlsplit(v)
            path = p.path
            if self.prefix and p.scheme.lower() in ("https", "http"):
                path = path[len(self.prefix):]
        else:
            m = re.match(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:/?(.*)$", v)
            path = m.group(1) if m else ""
        path = re.sub(r"\.git$", "", path.strip("/"))
        segs = path.split("/")
        if not 2 <= len(segs) <= self.max_path_segments or not all(
                _SEG.match(s) and set(s) != {"."} and s != "-" for s in segs):
            return None
        return path

    def web_url(self, path: str) -> str:
        """The page a person opens for ``path`` (a repository or a group)."""
        return f"{self.base_url}/{path.strip('/')}"

    def clone_url(self, path: str) -> str:
        return f"{self.base_url}/{path.strip('/')}.git"

    def ssh_url(self, path: str) -> str:
        """The scp-like ssh form a deploy key authenticates against."""
        return f"git@{self.host}:{path.strip('/')}.git"

    def namespace(self, owner: Optional[str]) -> Optional[str]:
        """``owner`` validated as this host's owner, org or group (``None`` when empty), or ``ValueError``."""
        v = (owner or "").strip().strip("/")
        if not v:
            return None
        segs = v.split("/")
        if (not self.nested_groups and len(segs) != 1) or len(segs) > self.max_path_segments - 1 or any(
                not _SEG.match(s) or set(s) == {"."} for s in segs):
            raise ValueError("invalid org — a GitHub org name, or a GitLab group path like group/subgroup")
        return v

    def deploy_keys_url(self, path: str) -> Optional[str]:
        """Where a person adds a deploy key to the repository at ``path``; ``None`` when unknown."""
        return None

    def describe(self) -> dict:
        """What a person and an agent may know about this provider: never a credential."""
        return {"name": self.name, "kind": self.kind, "label": self.label, "base_url": self.base_url,
                "host": self.netloc(), "nested_groups": self.nested_groups, "can_create": self.can_create}

    # -- API half ----------------------------------------------------------------------------------
    def ssl_context(self) -> ssl.SSLContext:
        """TLS for API calls: the system trust, or exactly the configured private CA."""
        return ssl.create_default_context(cafile=self.ca_file) if self.ca_file else ssl.create_default_context()

    def opener(self):
        """An opener that refuses redirects and trusts this provider's CA (tests inject their own)."""
        if self._opener is not None:
            return self._opener
        return urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=self.ssl_context()))

    def call(self, method: str, url: str, headers: dict, body: Optional[dict] = None) -> Any:
        """One JSON API call. Raises ``urllib.error.HTTPError`` / ``URLError`` for the adapter."""
        req = urllib.request.Request(url, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers)
        with self.opener().open(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")

    @abc.abstractmethod
    def create_repo(self, token: str, name: str, private: bool, owner: Optional[str]) -> str:
        """Create ``name`` under ``owner`` (the token's own account when ``None``); return the
        token-free https clone URL. Raises :class:`GitProviderError`."""

    @abc.abstractmethod
    def verify_token(self, token: str) -> str:
        """The account the token belongs to. Raises :class:`GitProviderError` (``token_rejected``…)."""

    def __repr__(self) -> str:  # never anything secret
        return f"<{type(self).__name__} {self.name} {self.base_url}>"


def error_message(exc: urllib.error.HTTPError) -> str:
    """The ``message`` an API error body carries, as text."""
    try:
        payload = json.loads(exc.read() or b"{}")
    except (ValueError, OSError):
        return ""
    if not isinstance(payload, dict):
        return ""
    msg = payload.get("message") or payload.get("error") or ""
    return json.dumps(msg) if isinstance(msg, (dict, list)) else str(msg)


class BasicHost(GitProvider):
    """A host the operator named only to shape its token (``<user>:<token>``). It creates nothing."""

    kind = BASIC
    max_path_segments = MAX_PATH_SEGMENTS
    nested_groups = True
    can_create = False

    def __init__(self, name: str, base_url: str, username: str, **kw):
        super().__init__(name, base_url, **kw)
        self.token_user = username

    def default_api_url(self) -> str:
        return self.base_url

    def create_repo(self, token, name, private, owner):
        raise GitProviderError(f"{self.label} is not a place this deployment creates repositories",
                               fault="unsupported", provider=self.name)

    def verify_token(self, token):
        raise GitProviderError(f"{self.label} has no token check", fault="unsupported", provider=self.name)


# ── configuration → registry ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Provider:
    """A host's token shape, as the comma list states it. ``username`` is set for ``basic`` only."""

    kind: str
    username: Optional[str] = None


DEFAULT = Provider(GITHUB)


def parse_providers(raw: Optional[str]) -> dict[str, Provider]:
    """The comma-list spelling (``<host>=<provider>``) → ``{hostname: Provider}``. Raises
    ``ValueError`` naming the bad entry (never a secret: the value is host names and provider words)."""
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


def _ca_file_for(name: str, entry: dict) -> Optional[str]:
    """``caFile`` as given, or ``caBundle`` (PEM) written once to a private file named by its hash."""
    ca_file, bundle = entry.get("caFile"), entry.get("caBundle")
    if ca_file and bundle:
        raise ValueError(f"{PROVIDERS_ENV}: provider '{name}' sets both caFile and caBundle; give one")
    if ca_file:
        if not isinstance(ca_file, str) or not os.path.isabs(ca_file):
            raise ValueError(f"{PROVIDERS_ENV}: provider '{name}' caFile must be an absolute path")
        return ca_file
    if not bundle:
        return None
    if not isinstance(bundle, str) or "-----BEGIN CERTIFICATE-----" not in bundle:
        raise ValueError(f"{PROVIDERS_ENV}: provider '{name}' caBundle is not a PEM certificate")
    pem = bundle if bundle.endswith("\n") else bundle + "\n"
    d = os.path.join(tempfile.gettempdir(), "vexa-git-ca")
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, f"{name}-{hashlib.sha256(pem.encode()).hexdigest()[:16]}.pem")
    if not os.path.exists(path):
        fd, tmp = tempfile.mkstemp(dir=d)
        with os.fdopen(fd, "w") as f:
            f.write(pem)
        os.replace(tmp, path)
    return path


def _build(kind: str, name: str, base_url: str, **kw) -> GitProvider:
    # The adapters import this module for the port, so they are imported here.
    if kind == GITHUB:
        from shared.git_provider_github import GitHubProvider
        return GitHubProvider(name, base_url, **kw)
    if kind == GITLAB:
        from shared.git_provider_gitlab import GitLabProvider
        return GitLabProvider(name, base_url, **kw)
    raise ValueError(f"{PROVIDERS_ENV}: provider '{name}' kind '{kind}'; use github or gitlab")


class Registry:
    """The deployment's providers, the built-in GitHub first."""

    def __init__(self, providers: list[GitProvider]):
        self._list = list(providers)
        self._by_name = {p.name: p for p in self._list}

    def all(self) -> list[GitProvider]:
        return list(self._list)

    def names(self) -> list[str]:
        return [p.name for p in self._list]

    def by_name(self, name: Optional[str]) -> Optional[GitProvider]:
        return self._by_name.get((name or "").strip().lower())

    def default(self) -> GitProvider:
        return self._by_name[DEFAULT_NAME]

    def for_url(self, url_or_host: str) -> Optional[GitProvider]:
        """The provider ``url_or_host`` is on — the longest base path wins — or ``None``."""
        hits = [p for p in self._list if p.matches(url_or_host)]
        return max(hits, key=lambda p: len(p.prefix)) if hits else None

    def for_host(self, host: str) -> Optional[GitProvider]:
        """The provider a bare ``host[:port]`` names (no path), or ``None``."""
        h = (host or "").strip().lower().rstrip(".")
        return next((p for p in self._list if p.netloc() == h or (p.host == h and not p.port)), None)


def parse_registry(raw: Optional[str]) -> Registry:
    """``VEXA_GIT_PROVIDERS`` (JSON list or comma list) → :class:`Registry`. Raises ``ValueError``."""
    from shared.git_provider_github import builtin_github
    text = (raw or "").strip()
    out: list[GitProvider] = []
    if text.startswith("[") or text.startswith("{"):
        try:
            entries = json.loads(text)
        except ValueError:
            raise ValueError(f"{PROVIDERS_ENV}: not a JSON list of providers") from None
        if not isinstance(entries, list):
            raise ValueError(f"{PROVIDERS_ENV}: a JSON list of providers, each with name, kind and baseUrl")
        for e in entries:
            if not isinstance(e, dict):
                raise ValueError(f"{PROVIDERS_ENV}: every provider is an object with name, kind and baseUrl")
            name = str(e.get("name") or "").strip().lower()
            unknown = set(e) - {"name", "kind", "baseUrl", "caBundle", "caFile", "label", "apiUrl"}
            if unknown:
                raise ValueError(f"{PROVIDERS_ENV}: provider '{name}' has unknown keys {sorted(unknown)}")
            if not _NAME.match(name):
                raise ValueError(f"{PROVIDERS_ENV}: provider name '{name}' — use lowercase letters, digits, '.' and '-'")
            kind = str(e.get("kind") or "").strip().lower()
            if kind not in PROVIDER_KINDS:
                raise ValueError(f"{PROVIDERS_ENV}: provider '{name}' kind '{kind}'; use github or gitlab")
            api = e.get("apiUrl")
            if api is not None and (not isinstance(api, str) or not api.startswith("https://")):
                raise ValueError(f"{PROVIDERS_ENV}: provider '{name}' apiUrl must be https://")
            label = e.get("label")
            if label is not None and (not isinstance(label, str) or not 0 < len(label) <= 60):
                raise ValueError(f"{PROVIDERS_ENV}: provider '{name}' label must be 1-60 characters")
            out.append(_build(kind, name, str(e.get("baseUrl") or ""), label=label,
                              ca_file=_ca_file_for(name, e), api_url=api))
    else:
        for host, p in parse_providers(text).items():
            if p.kind == BASIC:
                out.append(BasicHost(host, f"https://{host}", p.username or ""))
            else:
                out.append(_build(p.kind, host, f"https://{host}"))
    names = [p.name for p in out]
    if len(set(names)) != len(names):
        raise ValueError(f"{PROVIDERS_ENV}: a provider name is listed twice")
    bases = [p.base_url for p in out]
    if len(set(bases)) != len(bases):
        raise ValueError(f"{PROVIDERS_ENV}: two providers share one baseUrl")
    if DEFAULT_NAME not in names:
        gh = builtin_github()
        if gh.base_url not in bases:
            out.insert(0, gh)
    if DEFAULT_NAME not in [p.name for p in out]:
        raise ValueError(f"{PROVIDERS_ENV}: GitHub's own address may only be configured as the provider "
                         f"named '{DEFAULT_NAME}'")
    return Registry(out)


_CACHE: dict[str, Registry] = {}


def registry() -> Registry:
    """The deployment's providers, read from the environment (boot already validated it)."""
    raw = os.environ.get(PROVIDERS_ENV, "")
    reg = _CACHE.get(raw)
    if reg is None:
        _CACHE.clear()
        reg = _CACHE[raw] = parse_registry(raw)
    return reg


def providers() -> dict[str, Provider]:
    """The token-shape view: ``{hostname: Provider}`` for every configured provider."""
    return {p.host: Provider(p.kind, p.token_user if p.kind == BASIC else None) for p in registry().all()}


# ── URL helpers + token presentation ──────────────────────────────────────────────────────────────

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


def for_url(url_or_host: str) -> Optional[GitProvider]:
    """The configured provider ``url_or_host`` is on, or ``None``."""
    return registry().for_url(url_or_host)


def provider_for(url_or_host: str) -> Provider:
    """The token shape the host of ``url_or_host`` takes: its provider's, else GitHub's bare token
    (how every unlisted host behaved before providers were configurable)."""
    reg = registry()
    p = reg.for_url(url_or_host) or next((q for q in reg.all() if q.host == hostname_of(url_or_host)), None)
    if p is None:
        return DEFAULT
    return Provider(p.kind, p.token_user if p.kind == BASIC else None)


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
        p = provider_for(url)
        if p.kind == GITLAB:
            user = GITLAB_USERNAME
        elif p.kind == BASIC:
            user = p.username
    if user is None:
        return quote(secret, safe="")
    return f"{quote(user, safe='')}:{quote(secret, safe='')}"


def ca_file_for(url: str) -> Optional[str]:
    """The private CA file of the provider ``url`` is on, for git's TLS (``GIT_SSL_CAINFO``)."""
    p = registry().for_url(url)
    return p.ca_file if p else None
