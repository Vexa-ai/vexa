"""web_tools.py — the openai-agent harness's reach onto the open web: ``WebSearch`` + ``WebFetch``.

The harness's file tools made the worker able to read the workspace and nothing else, so a turn whose
whole job is research ("find out who this person is, then write the entity") had no way to find out
anything. The onboarding playbook has said *research first, ask last* since it shipped; under
`openai-agent` there was nothing behind the sentence.

**SEARCH IS AN ADAPTER, NOT A DEPENDENCY, AND THAT IS A LICENCE DECISION.** The obvious backend for a
self-hosted deployment is SearXNG, which is **AGPL-3.0** — a licence this product will not carry.
Nothing about search is vendored here: the operator runs an endpoint and names it, this module speaks
its dialect, and the repo ships no image, no service, no compose profile and no third-party code.
Two variables are the whole interface:

    VEXA_SEARCH_URL      the endpoint (unset ⇒ WebSearch is simply not attached)
    VEXA_SEARCH_DIALECT  which wire format it speaks — `searxng` (default) | `brave`
    VEXA_SEARCH_API_KEY  the credential, for a dialect that needs one (brave does; searxng does not)

Adding a third dialect is one ~20-line function in ``_DIALECTS`` — take a client, a URL, a query and
a count, return ``[{"title","url","snippet"}]``. That is deliberately the smallest thing that can be
called a plug point: a registry of classes would be a framework for two entries.

``WebFetch`` needs no backend and is therefore ALWAYS attached. It carries the guard search does not
need: a URL the model chose is an outbound destination a non-operator picked, so it is refused when it
resolves to loopback, link-local (cloud metadata), private or reserved space — the SSRF shape
``control_plane/model_endpoint.py`` refuses for a subject-pinned model endpoint. That module is NOT
imported: the worker image ships `worker/`, `llm/`, `shared/` and `contracts/` and deliberately not
`control_plane/`, so importing it would be an ImportError in the only process that runs this code.
The rule is re-stated here in stdlib ``ipaddress``, and the one exemption is the operator's own
``VEXA_SEARCH_URL`` host — a search endpoint on the deployment's private network is a destination the
operator chose, and refusing to read a result page from the endpoint we just queried is a rule with
no threat behind it.

Both tools obey the harness's existing discipline rather than inventing their own: the loop counts
every call against ``VEXA_AGENT_MAX_TOOL_CALLS`` and trims every result to ``_TOOL_RESULT_MAX_CHARS``,
so nothing here needs a second budget. What is here is the *transport* ceiling the loop cannot see —
a 15s timeout and a hard body cap, so a slow or enormous page costs the turn its call and not its
wall clock.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
from html import unescape
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import httpx

log = logging.getLogger(__name__)

# ── configuration (env only — this module owns no product imports, same rule as the harness) ─────

URL_ENV = "VEXA_SEARCH_URL"
DIALECT_ENV = "VEXA_SEARCH_DIALECT"
API_KEY_ENV = "VEXA_SEARCH_API_KEY"

DEFAULT_DIALECT = "searxng"

#: transport ceilings. The per-turn budget is the loop's; these bound ONE call.
FETCH_TIMEOUT = 15.0
SEARCH_TIMEOUT = 15.0
MAX_BODY_BYTES = 2_000_000          # a page bigger than this is not being read, it is being downloaded
MAX_REDIRECTS = 3                   # every hop is re-checked against the SSRF guard
DEFAULT_MAX_RESULTS = 8
MAX_RESULTS_CAP = 25
DEFAULT_FETCH_CHARS = 12_000
MAX_FETCH_CHARS = 100_000           # the loop trims to 24k anyway; this stops a pathological arg

_UA = {"User-Agent": "vexa-agent/0.12 (+https://vexa.ai)", "Accept": "*/*"}


def search_url() -> str:
    return (os.environ.get(URL_ENV) or "").strip()


def search_dialect() -> str:
    return ((os.environ.get(DIALECT_ENV) or "").strip() or DEFAULT_DIALECT).lower()


def search_api_key() -> str:
    return (os.environ.get(API_KEY_ENV) or "").strip()


def search_configured() -> bool:
    """Whether a search BACKEND exists for this deployment.

    The harness's rule is that a tool it cannot serve is simply not attached and the turn's tool list
    says so — a ``WebSearch`` that is advertised and always fails teaches the model to stop trying,
    which costs the turns that could have used it once an operator configures one."""
    return bool(search_url())


def _host(url: str) -> str:
    try:
        return (urlsplit((url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


# ── the dialects ────────────────────────────────────────────────────────────────────────────────
# One function per wire format: (client, url, query, n, api_key) -> [{"title","url","snippet"}].
# The URL is the OPERATOR'S, verbatim; each dialect appends its own default path only when the
# operator gave a bare origin, because "http://searxng:8080" is what an operator naturally writes.

def _endpoint(url: str, default_path: str) -> str:
    parts = urlsplit(url)
    if parts.path in ("", "/"):
        return url.rstrip("/") + default_path
    return url


def _searxng(client: httpx.Client, url: str, query: str, n: int, api_key: str) -> list[dict]:
    """SearXNG's JSON output (`?format=json`, which the operator must enable in their settings.yml —
    it is off by default and the endpoint answers 403 when it is not)."""
    r = client.get(_endpoint(url, "/search"),
                   params={"q": query, "format": "json"},
                   headers=_UA, timeout=SEARCH_TIMEOUT)
    r.raise_for_status()
    hits = []
    for item in (r.json().get("results") or [])[:n]:
        if not isinstance(item, dict):
            continue
        hits.append({"title": str(item.get("title") or "").strip(),
                     "url": str(item.get("url") or "").strip(),
                     "snippet": str(item.get("content") or "").strip()})
    return hits


def _brave(client: httpx.Client, url: str, query: str, n: int, api_key: str) -> list[dict]:
    """The Brave Search API: `X-Subscription-Token`, results under `web.results[]`."""
    headers = {**_UA, "Accept": "application/json", "X-Subscription-Token": api_key}
    r = client.get(_endpoint(url, "/res/v1/web/search"),
                   params={"q": query, "count": n}, headers=headers, timeout=SEARCH_TIMEOUT)
    r.raise_for_status()
    hits = []
    for item in ((r.json().get("web") or {}).get("results") or [])[:n]:
        if not isinstance(item, dict):
            continue
        hits.append({"title": str(item.get("title") or "").strip(),
                     "url": str(item.get("url") or "").strip(),
                     "snippet": str(item.get("description") or "").strip()})
    return hits


_DIALECTS: dict[str, Callable[[httpx.Client, str, str, int, str], list[dict]]] = {
    "searxng": _searxng,
    "brave": _brave,
}


# ── the SSRF guard (WebFetch only; the search endpoint is the operator's own choice) ─────────────

def _blocked_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True                     # unreadable is not the same as safe
    return bool(ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved
                or ip.is_unspecified or ip.is_multicast)


def _resolve(host: str) -> list[str]:
    """Every address ``host`` resolves to. Separate function so a test can play DNS."""
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def fetch_refusal(url: str, resolve: Optional[Callable[[str], list[str]]] = None) -> Optional[str]:
    """``None`` when the model may fetch this URL, else the reason it may not — phrased for the
    MODEL, which is the reader that can act on it by choosing a different URL."""
    resolve = resolve or _resolve
    raw = (url or "").strip()
    if not raw:
        return "WebFetch needs a url"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return f"{raw!r} is not a URL"
    if parts.scheme.lower() not in ("http", "https"):
        return f"WebFetch only follows http and https — {raw!r} is not one"
    host = (parts.hostname or "").lower()
    if not host:
        return f"{raw!r} names no host"
    # THE ONE EXEMPTION: the operator's own search endpoint. It is routinely a private-network
    # service name, the operator named it themselves, and the search tool already talks to it.
    if host and host == _host(search_url()):
        return None
    if host == "localhost" or host.endswith(".localhost"):
        return f"WebFetch refuses {host!r} — the open web only, never this deployment's own network"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            return (f"WebFetch refuses the single-label host {host!r} — that is an internal service "
                    "name, not a public site")
        try:
            addrs = resolve(host)
        except OSError as exc:
            return f"WebFetch could not resolve {host!r} ({type(exc).__name__})"
        if not addrs:
            return f"WebFetch could not resolve {host!r}"
        if any(_blocked_ip(a) for a in addrs):
            return (f"WebFetch refuses {host!r} — it resolves into loopback/link-local/private "
                    "address space, which is this deployment's own network and not the open web")
        return None
    if _blocked_ip(host):
        return (f"WebFetch refuses {host!r} — loopback/link-local/private/reserved addresses are "
                "this deployment's own network, not the open web")
    return None


# ── HTML → readable text (dependency-free: a parser would be a new dep in the shipped image) ─────

_DROP = ("script", "style", "noscript", "template", "svg", "canvas", "iframe", "form",
         "nav", "header", "footer", "aside")
_BLOCK = r"(?:p|div|br|hr|li|tr|td|th|h[1-6]|section|article|main|ul|ol|dl|dt|dd|table|blockquote|pre|figure)"


def _collapse(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def readable(html: str) -> tuple[str, str]:
    """``(title, text)`` from an HTML document. Keeps the title, drops the chrome, collapses the
    whitespace — enough for a model to read a page, and honest about being an extractor rather than
    a renderer."""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = _collapse(unescape(re.sub(r"<[^>]+>", " ", m.group(1))))
    body = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    body = re.sub(r"<head\b.*?</head\s*>", " ", body, flags=re.I | re.S)
    for tag in _DROP:
        body = re.sub(rf"<{tag}\b.*?</{tag}\s*>", " ", body, flags=re.I | re.S)
        body = re.sub(rf"<{tag}\b[^>]*/?>", " ", body, flags=re.I)
    body = re.sub(rf"</?{_BLOCK}\b[^>]*>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    return title, _collapse(unescape(body))


def _charset(content_type: str) -> str:
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    return (m.group(1) if m else "utf-8")


# ── the two tools ───────────────────────────────────────────────────────────────────────────────

def _client(existing: Optional[httpx.Client], timeout: float) -> tuple[httpx.Client, bool]:
    if existing is not None:
        return existing, False
    return httpx.Client(timeout=timeout, follow_redirects=False), True


def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS, *,
               client: Optional[httpx.Client] = None) -> tuple[bool, str]:
    """``[{title,url,snippet}]`` as JSON, via whichever dialect the operator configured."""
    query = str(query or "").strip()
    if not query:
        return False, "WebSearch needs a query"
    url = search_url()
    if not url:
        return False, (f"no search backend is configured — set {URL_ENV} to an endpoint this "
                       f"deployment may query (and {DIALECT_ENV} if it is not {DEFAULT_DIALECT})")
    dialect = search_dialect()
    fn = _DIALECTS.get(dialect)
    if fn is None:
        return False, (f"{DIALECT_ENV}={dialect!r} is not a dialect this build speaks — "
                       f"known: {', '.join(sorted(_DIALECTS))}")
    try:
        n = max(1, min(int(max_results or DEFAULT_MAX_RESULTS), MAX_RESULTS_CAP))
    except (TypeError, ValueError):
        n = DEFAULT_MAX_RESULTS
    cli, own = _client(client, SEARCH_TIMEOUT)
    try:
        hits = fn(cli, url, query, n, search_api_key())
    except httpx.HTTPStatusError as exc:
        return False, (f"the search endpoint answered {exc.response.status_code} — check that "
                       f"{URL_ENV} is right and that its JSON output is enabled")
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        return False, f"search failed: {type(exc).__name__}: {exc}"
    finally:
        if own:
            cli.close()
    if not hits:
        return True, json.dumps({"query": query, "results": []})
    return True, json.dumps({"query": query, "results": hits}, ensure_ascii=False)


def web_fetch(url: str, max_chars: int = DEFAULT_FETCH_CHARS, *,
              client: Optional[httpx.Client] = None,
              resolve: Optional[Callable[[str], list[str]]] = None) -> tuple[bool, str]:
    """GET one page and hand back readable text: ``{url, final_url, status, title, text}`` as JSON."""
    target = str(url or "").strip()
    try:
        cap = max(200, min(int(max_chars or DEFAULT_FETCH_CHARS), MAX_FETCH_CHARS))
    except (TypeError, ValueError):
        cap = DEFAULT_FETCH_CHARS
    cli, own = _client(client, FETCH_TIMEOUT)
    try:
        for hop in range(MAX_REDIRECTS + 1):
            refusal = fetch_refusal(target, resolve)
            if refusal:
                return False, refusal
            try:
                with cli.stream("GET", target, headers=_UA, timeout=FETCH_TIMEOUT) as r:
                    location = r.headers.get("location")
                    if r.status_code in (301, 302, 303, 307, 308) and location and hop < MAX_REDIRECTS:
                        target = urljoin(str(r.request.url), location)
                        continue            # EVERY hop is re-checked — a redirect into 169.254.x is
                        # the classic way past a guard that only reads the URL the model typed
                    status = r.status_code
                    ctype = (r.headers.get("content-type") or "")
                    final_url = str(r.url)
                    buf = bytearray()
                    truncated = False
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) >= MAX_BODY_BYTES:
                            truncated = True
                            break
            except httpx.HTTPError as exc:
                return False, f"WebFetch failed: {type(exc).__name__}: {exc}"
            mime = ctype.split(";")[0].strip().lower()
            body = bytes(buf).decode(_charset(ctype), errors="replace")
            if mime.startswith("text/html") or mime in ("application/xhtml+xml", ""):
                title, text = readable(body)
            elif mime.startswith("text/") or mime in ("application/json", "application/xml",
                                                      "text/xml", "application/rss+xml"):
                title, text = "", _collapse(body)
            else:
                title, text = "", f"[{mime or 'unknown content type'}, {len(buf)} bytes — not text]"
            out = {"url": str(url or "").strip(), "final_url": final_url, "status": status,
                   "title": title, "text": text[:cap]}
            if truncated or len(text) > cap:
                out["truncated"] = True
            return True, json.dumps(out, ensure_ascii=False)
        return False, f"WebFetch gave up after {MAX_REDIRECTS} redirects"
    finally:
        if own:
            cli.close()
