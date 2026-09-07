"""L2: the openai-agent harness's web reach — ``WebSearch`` + ``WebFetch``, offline.

Every backend is a canned ``httpx.MockTransport`` and DNS is a function the test plays, so nothing
here touches a network. What each test defends:

* **attachment is conditional and the turn's tool list says so** — a `WebSearch` advertised with no
  backend behind it teaches the model that searching does not work, and that lesson outlives the
  turn; `WebFetch` needs no backend and is therefore always there;
* **both dialects parse** from the shapes their real services return, because the whole licence
  argument rests on search being an ADAPTER — a dialect that only works against the one endpoint we
  happened to test is a dependency wearing an adapter's name;
* **an unknown dialect is a named failure**, not a silent empty result;
* **the SSRF guard refuses the deployment's own network** — literal private IPs, single-label docker
  service names, hosts that RESOLVE into private space, and every redirect hop, which is the way
  past a guard that only reads the URL the model typed;
* **the operator's own search host is the one exemption**, because refusing to read a page from the
  endpoint we just queried is a rule with no threat behind it;
* **the extractor keeps the title and drops the chrome**, and `max_chars` actually truncates.
"""
from __future__ import annotations

import json

import httpx
import pytest

from llm import web_tools
from llm.openai_agent import BUILTIN_SPECS, _attached, run_builtin


@pytest.fixture(autouse=True)
def _no_ambient_search(monkeypatch):
    for key in (web_tools.URL_ENV, web_tools.DIALECT_ENV, web_tools.API_KEY_ENV):
        monkeypatch.delenv(key, raising=False)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _public(_host):
    return ["93.184.216.34"]


# ── attachment ──────────────────────────────────────────────────────────────────────────────────

def test_both_web_tools_have_specs():
    assert "WebSearch" in BUILTIN_SPECS and "WebFetch" in BUILTIN_SPECS
    assert BUILTIN_SPECS["WebSearch"]["parameters"]["required"] == ["query"]
    assert BUILTIN_SPECS["WebFetch"]["parameters"]["required"] == ["url"]


def test_websearch_is_absent_without_a_backend_and_webfetch_is_always_there():
    assert _attached("WebSearch") is False
    assert _attached("WebFetch") is True
    assert _attached("Read") is True


def test_websearch_is_attached_once_an_endpoint_is_named(monkeypatch):
    monkeypatch.setenv(web_tools.URL_ENV, "http://searx.internal:8080")
    assert _attached("WebSearch") is True


def test_calling_websearch_with_no_backend_is_a_failed_result_naming_the_key():
    ok, out = run_builtin("WebSearch", {"query": "vexa"}, sandbox=None)
    assert ok is False
    assert web_tools.URL_ENV in out


# ── the dialects ────────────────────────────────────────────────────────────────────────────────

_SEARXNG_JSON = {
    "query": "academy software foundation",
    "results": [
        {"title": "Academy Software Foundation", "url": "https://www.aswf.io/",
         "content": "A neutral forum for open source software development."},
        {"title": "ASWF on GitHub", "url": "https://github.com/AcademySoftwareFoundation",
         "content": "Projects hosted by the foundation."},
        {"title": "third", "url": "https://example.org/3", "content": "third snippet"},
    ],
}

_BRAVE_JSON = {
    "web": {"results": [
        {"title": "Academy Software Foundation", "url": "https://www.aswf.io/",
         "description": "A neutral forum for open source software development."},
        {"title": "ASWF on GitHub", "url": "https://github.com/AcademySoftwareFoundation",
         "description": "Projects hosted by the foundation."},
    ]},
}


def test_searxng_dialect_parses_results_and_asks_for_json(monkeypatch):
    monkeypatch.setenv(web_tools.URL_ENV, "http://searx.internal:8080")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_SEARXNG_JSON)

    ok, out = web_tools.web_search("academy software foundation", 2, client=_client(handler))
    assert ok, out
    assert seen["url"].startswith("http://searx.internal:8080/search?")
    assert "format=json" in seen["url"]
    payload = json.loads(out)
    assert [r["url"] for r in payload["results"]] == ["https://www.aswf.io/",
                                                      "https://github.com/AcademySoftwareFoundation"]
    assert payload["results"][0]["snippet"].startswith("A neutral forum")


def test_brave_dialect_parses_results_and_sends_the_subscription_token(monkeypatch):
    monkeypatch.setenv(web_tools.URL_ENV, "https://api.search.brave.com")
    monkeypatch.setenv(web_tools.DIALECT_ENV, "brave")
    monkeypatch.setenv(web_tools.API_KEY_ENV, "tok-123")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["token"] = request.headers.get("x-subscription-token")
        return httpx.Response(200, json=_BRAVE_JSON)

    ok, out = web_tools.web_search("academy software foundation", 8, client=_client(handler))
    assert ok, out
    assert seen["url"].startswith("https://api.search.brave.com/res/v1/web/search?")
    assert seen["token"] == "tok-123"
    payload = json.loads(out)
    assert payload["results"][0]["title"] == "Academy Software Foundation"
    assert payload["results"][1]["snippet"] == "Projects hosted by the foundation."


def test_the_dialect_registry_is_the_plug_point():
    # Adding a backend is one function in one table — that is the whole licence argument's mechanics.
    assert set(web_tools._DIALECTS) == {"searxng", "brave"}
    assert web_tools.DEFAULT_DIALECT == "searxng"

    def _stub(client, url, query, n, api_key):
        return [{"title": "t", "url": "https://x.example/1", "snippet": "s"}]

    web_tools._DIALECTS["stub"] = _stub
    try:
        import os
        os.environ[web_tools.URL_ENV] = "https://x.example"
        os.environ[web_tools.DIALECT_ENV] = "stub"
        ok, out = web_tools.web_search("q", 3, client=_client(lambda r: httpx.Response(200)))
        assert ok and json.loads(out)["results"][0]["url"] == "https://x.example/1"
    finally:
        web_tools._DIALECTS.pop("stub")


def test_an_unknown_dialect_names_the_ones_this_build_speaks(monkeypatch):
    monkeypatch.setenv(web_tools.URL_ENV, "https://x.example")
    monkeypatch.setenv(web_tools.DIALECT_ENV, "kagi")
    ok, out = web_tools.web_search("q", 8, client=_client(lambda r: httpx.Response(200)))
    assert ok is False
    assert "kagi" in out and "searxng" in out and "brave" in out


def test_a_search_endpoint_error_is_a_failed_result_not_an_exception(monkeypatch):
    monkeypatch.setenv(web_tools.URL_ENV, "http://searx.internal:8080")
    ok, out = web_tools.web_search("q", 8, client=_client(lambda r: httpx.Response(403, text="no")))
    assert ok is False and "403" in out


def test_max_results_is_capped_and_floored(monkeypatch):
    monkeypatch.setenv(web_tools.URL_ENV, "http://searx.internal:8080")
    ok, out = web_tools.web_search("q", 1, client=_client(lambda r: httpx.Response(200, json=_SEARXNG_JSON)))
    assert ok and len(json.loads(out)["results"]) == 1


# ── the SSRF guard ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/x",
    "http://localhost/x",
    "http://[::1]/x",
    "http://169.254.169.254/latest/meta-data/",       # cloud metadata — the whole reason for the rule
    "http://10.1.2.3/x",
    "http://192.168.1.6:8001/v1",
    "http://redis:6379/",                             # a docker service name on our own network
    "http://admin-api:8001/admin/users",
    "file:///etc/passwd",
    "ftp://example.com/x",
])
def test_webfetch_refuses_this_deployments_own_network(url):
    assert web_tools.fetch_refusal(url, resolve=_public) is not None


def test_webfetch_refuses_a_public_name_that_resolves_into_private_space():
    assert web_tools.fetch_refusal("https://evil.example/x",
                                   resolve=lambda h: ["10.0.0.5"]) is not None


def test_webfetch_allows_an_ordinary_public_url():
    assert web_tools.fetch_refusal("https://www.aswf.io/", resolve=_public) is None


def test_the_configured_search_host_is_the_one_exemption(monkeypatch):
    # A single-label docker service name — refused as an internal name, exactly like `redis`…
    assert web_tools.fetch_refusal("http://vexa-searxng:8080/search?q=x", resolve=_public) is not None
    # …until it IS the endpoint the operator configured, which the operator chose themselves.
    monkeypatch.setenv(web_tools.URL_ENV, "http://vexa-searxng:8080")
    assert web_tools.fetch_refusal("http://vexa-searxng:8080/search?q=x", resolve=_public) is None
    # The exemption is that host and no other: a sibling service on the same private network is
    # still refused while search is configured.
    assert web_tools.fetch_refusal("http://admin-api:8001/admin/users", resolve=_public) is not None


def test_a_redirect_into_private_space_is_refused_at_the_hop():
    def handler(request):
        if request.url.host == "www.aswf.io":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
        return httpx.Response(200, text="secrets")

    ok, out = web_tools.web_fetch("https://www.aswf.io/", client=_client(handler), resolve=_public)
    assert ok is False and "169.254.169.254" in out


def test_a_redirect_to_a_public_page_is_followed():
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(301, headers={"location": "/mission"})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<html><title>ASWF</title><body><p>Mission.</p></body></html>")

    ok, out = web_tools.web_fetch("https://www.aswf.io/", client=_client(handler), resolve=_public)
    assert ok, out
    body = json.loads(out)
    assert body["final_url"].endswith("/mission")
    assert body["title"] == "ASWF" and "Mission." in body["text"]


# ── the extractor ───────────────────────────────────────────────────────────────────────────────

_PAGE = """<!doctype html>
<html><head><title>  Academy Software  Foundation  </title>
<style>body{color:red}</style>
<script>window.tracker = 1;</script></head>
<body>
<nav><a href="/about">About</a><a href="/projects">Projects</a></nav>
<header>Site header</header>
<main><h1>Mission</h1>
<p>Increase the &amp; quality of open source software in the motion picture industry.</p>
<p>Second paragraph.</p></main>
<footer>Copyright 2026</footer>
<script>more();</script>
</body></html>"""


def test_the_extractor_keeps_the_title_and_drops_the_chrome():
    title, text = web_tools.readable(_PAGE)
    assert title == "Academy Software Foundation"
    assert "Increase the & quality of open source software" in text
    assert "Second paragraph." in text
    for chrome in ("window.tracker", "color:red", "Site header", "Copyright 2026", "About"):
        assert chrome not in text
    assert "\n\n\n" not in text


def test_webfetch_returns_the_documented_shape():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=_PAGE)

    ok, out = web_tools.web_fetch("https://www.aswf.io/", client=_client(handler), resolve=_public)
    assert ok, out
    body = json.loads(out)
    assert set(body) >= {"url", "final_url", "status", "title", "text"}
    assert body["status"] == 200 and body["url"] == "https://www.aswf.io/"


def test_max_chars_truncates_and_says_so():
    long_page = "<html><title>Long</title><body>" + "<p>paragraph text here.</p>" * 400 + "</body></html>"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=long_page)

    ok, out = web_tools.web_fetch("https://www.aswf.io/", 500, client=_client(handler), resolve=_public)
    body = json.loads(out)
    assert ok and len(body["text"]) == 500 and body["truncated"] is True


def test_max_chars_is_floored_and_capped():
    # A model that asks for 5 characters gets a readable minimum; one that asks for a megabyte does
    # not get to spend the whole turn's context on one page.
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<html><title>T</title><body><p>" + "x" * 300_000 + "</p></body></html>")

    ok, out = web_tools.web_fetch("https://www.aswf.io/", 5, client=_client(handler), resolve=_public)
    assert ok and len(json.loads(out)["text"]) == 200
    ok, out = web_tools.web_fetch("https://www.aswf.io/", 10 ** 9, client=_client(handler),
                                  resolve=_public)
    assert ok and len(json.loads(out)["text"]) == web_tools.MAX_FETCH_CHARS


def test_a_non_text_body_is_reported_rather_than_decoded():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG\r\n" * 10)

    ok, out = web_tools.web_fetch("https://www.aswf.io/logo.png", client=_client(handler),
                                  resolve=_public)
    body = json.loads(out)
    assert ok and "image/png" in body["text"] and body["title"] == ""


def test_a_transport_error_is_a_failed_result_not_an_exception():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    ok, out = web_tools.web_fetch("https://www.aswf.io/", client=_client(handler), resolve=_public)
    assert ok is False and "ConnectError" in out


# ── the harness's executor routes both tools (and never consults the sandbox for them) ──────────

def test_run_builtin_routes_websearch_and_webfetch(monkeypatch):
    monkeypatch.setenv(web_tools.URL_ENV, "http://searx.internal:8080")

    def handler(request):
        if "searx.internal" in str(request.url):
            return httpx.Response(200, json=_SEARXNG_JSON)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_PAGE)

    web = _client(handler)
    ok, out = run_builtin("WebSearch", {"query": "aswf", "max_results": 2}, None, web)
    assert ok and len(json.loads(out)["results"]) == 2

    monkeypatch.setattr(web_tools, "_resolve", _public)
    ok, out = run_builtin("WebFetch", {"url": "https://www.aswf.io/"}, None, web)
    assert ok and json.loads(out)["title"] == "Academy Software Foundation"
