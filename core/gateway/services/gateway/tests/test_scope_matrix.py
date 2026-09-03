"""The per-route scope matrix, and the deny-by-default guard that keeps it complete.

Motivated by the rc.18 documented-API sweep against staging (finding B2): a key holding ONLY the
``browser`` scope returned 201 on ``POST /bots`` and scheduled a real bot container
(``mtg-26362-7c9e8908``), then stopped it with ``DELETE``. Same hole on ``GET /bots``,
``/bots/status``, ``/bots/{p}/{n}/chat``, ``/user/webhook`` and ``/user/calendar``, while
``/meetings``, ``/transcripts/*`` and ``/recordings`` refused it correctly — the mechanism existed
and those routes simply were not using it.

Two causes, one per family:
  * ``/bots`` was declared ``{"bot", "browser"}``, so ``browser`` alone satisfied it;
  * ``/user/*`` had no declaration at all, and an absent declaration meant "no check".

This module is the executable spec for both halves: a matrix of every protected route against every
single-scope key, and a guard proving a route with no declaration is denied rather than forwarded.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import gateway.app as app_module
from gateway import ROUTE_SCOPES, UNSCOPED_ROUTES, create_app, undeclared_routes
from gateway import app as gateway_app
from gateway import routes_manifest
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

AUTH = {"x-api-key": VALID_KEY}

# One concrete request per protected route template. Path params are filled with fixtures the
# downstream fake never looks at — the assertion is on the EDGE's verdict, which is reached before
# any forward. Every entry is checked against the router's own table by
# ``test_matrix_covers_every_declared_route`` below, so this list cannot silently drift.
CASES = [
    # method, url, route template
    ("GET", "/bots", "/bots"),
    ("POST", "/bots", "/bots"),
    ("GET", "/bots/status", "/bots/status"),
    ("DELETE", "/bots/google_meet/abc-defg-hij", "/bots/{platform}/{native_meeting_id}"),
    ("PUT", "/bots/google_meet/abc-defg-hij/config", "/bots/{platform}/{native_meeting_id}/config"),
    ("POST", "/bots/google_meet/abc-defg-hij/speak", "/bots/{platform}/{native_meeting_id}/speak"),
    ("GET", "/bots/google_meet/abc-defg-hij/chat", "/bots/{platform}/{native_meeting_id}/chat"),

    ("GET", "/meetings", "/meetings"),
    ("POST", "/meetings", "/meetings"),
    ("GET", "/meetings/42", "/meetings/{meeting_id}"),
    ("PATCH", "/meetings/42", "/meetings/{meeting_id}"),
    ("DELETE", "/meetings/42", "/meetings/{meeting_id}"),
    ("GET", "/transcripts/search", "/transcripts/search"),
    ("PATCH", "/meetings/google_meet/abc-defg-hij", "/meetings/{platform}/{native_meeting_id}"),
    ("DELETE", "/meetings/google_meet/abc-defg-hij", "/meetings/{platform}/{native_meeting_id}"),
    ("PUT", "/meetings/google_meet/abc-defg-hij/intent",
     "/meetings/{platform}/{native_meeting_id}/intent"),
    ("POST", "/meetings/google_meet/abc-defg-hij/annotate",
     "/meetings/{platform}/{native_meeting_id}/annotate"),
    ("POST", "/meetings/42/share", "/meetings/{meeting_id}/share"),
    ("POST", "/meetings/42/transcript-import", "/meetings/{meeting_id}/transcript-import"),
    ("POST", "/meetings/google_meet/abc-defg-hij/share",
     "/meetings/{platform}/{native_meeting_id}/share"),
    ("POST", "/meetings/google_meet/abc-defg-hij/workspace",
     "/meetings/{platform}/{native_meeting_id}/workspace"),
    ("GET", "/meetings/google_meet/abc-defg-hij/participants",
     "/meetings/{platform}/{native_meeting_id}/participants"),

    ("GET", "/transcripts/by-id/42", "/transcripts/by-id/{meeting_id}"),
    ("GET", "/transcripts/google_meet/abc-defg-hij", "/transcripts/{platform}/{native_meeting_id}"),
    ("POST", "/transcripts/by-id/42/share", "/transcripts/by-id/{meeting_id}/share"),
    ("POST", "/transcripts/google_meet/abc-defg-hij/share",
     "/transcripts/{platform}/{native_meeting_id}/share"),
    ("POST", "/transcripts/share/accept", "/transcripts/share/accept"),

    ("GET", "/recordings", "/recordings"),
    ("GET", "/recordings/5", "/recordings/{recording_id}"),
    ("GET", "/recordings/5/master", "/recordings/{recording_id}/master"),
    ("GET", "/recordings/5/media/9/raw", "/recordings/{recording_id}/media/{media_file_id}/raw"),
    ("GET", "/recordings/5/media/9/download",
     "/recordings/{recording_id}/media/{media_file_id}/download"),
    ("DELETE", "/recordings/5", "/recordings/{recording_id}"),

    ("GET", "/user/calendar", "/user/calendar"),
    ("PUT", "/user/calendar", "/user/calendar"),
    ("GET", "/user/calendar/sync", "/user/calendar/sync"),
    ("POST", "/user/calendar/sync", "/user/calendar/sync"),
    ("GET", "/user/calendars", "/user/calendars"),
    ("POST", "/user/calendars", "/user/calendars"),
    ("PATCH", "/user/calendars/work-1", "/user/calendars/{calendar_id}"),
    ("DELETE", "/user/calendars/work-1", "/user/calendars/{calendar_id}"),
    ("GET", "/user/calendars/work-1/sync", "/user/calendars/{calendar_id}/sync"),
    ("POST", "/user/calendars/work-1/sync", "/user/calendars/{calendar_id}/sync"),

    ("GET", "/user/webhook", "/user/webhook"),
    ("PUT", "/user/webhook", "/user/webhook"),
    ("GET", "/user/webhook/deliveries", "/user/webhook/deliveries"),
    ("GET", "/user/models", "/user/models"),
    ("PUT", "/user/models", "/user/models"),
    ("GET", "/user/transcription", "/user/transcription"),
    ("PUT", "/user/transcription", "/user/transcription"),

    ("POST", "/agent/chat", "/agent/chat"),
    ("GET", "/agent/meeting/stream", "/agent/meeting/stream"),
    ("GET", "/agent/sessions", "/agent/{path:path}"),
    ("POST", "/agent/routines", "/agent/{path:path}"),
    ("PUT", "/agent/workspace/swap", "/agent/{path:path}"),
    ("PATCH", "/agent/routines/x/enabled", "/agent/{path:path}"),
    ("DELETE", "/agent/routines/x", "/agent/{path:path}"),

    ("GET", "/mcp", "/mcp"),
    ("POST", "/mcp", "/mcp"),
    ("PUT", "/mcp", "/mcp"),
    ("PATCH", "/mcp", "/mcp"),
    ("DELETE", "/mcp", "/mcp"),
    ("OPTIONS", "/mcp", "/mcp"),
    ("GET", "/mcp/session", "/mcp/{path:path}"),
    ("POST", "/mcp/session", "/mcp/{path:path}"),
    ("PUT", "/mcp/session", "/mcp/{path:path}"),
    ("PATCH", "/mcp/session", "/mcp/{path:path}"),
    ("DELETE", "/mcp/session", "/mcp/{path:path}"),
    ("OPTIONS", "/mcp/session", "/mcp/{path:path}"),
]

SCOPES = ["bot", "tx", "browser"]

MATRIX = [
    (method, url, template, scope)
    for method, url, template in CASES
    for scope in SCOPES
]


def _client(scopes):
    """A gateway whose authorizer hands back a key carrying exactly ``scopes``."""
    app = create_app(
        FakeAuthorizer(user={"user_id": 7, "scopes": list(scopes), "max_concurrent": 3,
                             "email": "u@example.com"}),
        FakeDownstream(status_code=200, body={"ok": True}),
        FakeRedis(),
    )
    return TestClient(app)


def _request(client, method, url):
    body = {} if method in ("POST", "PUT", "PATCH") else None
    return client.request(method, url, headers=AUTH, json=body)


@pytest.mark.parametrize(
    "method,url,template,scope", MATRIX,
    ids=[f"{m} {u} [{s}]" for m, u, _t, s in MATRIX],
)
def test_scope_matrix(method, url, template, scope):
    """Every protected route × every single-scope key → allow or deny, per ROUTE_SCOPES.

    The matrix is derived from the SHIPPED table rather than restated, so the assertion is
    "the edge does what the declaration says" and the declaration is reviewed once, above.
    """
    allowed = scope in ROUTE_SCOPES[(method, template)]
    r = _request(_client([scope]), method, url)
    if allowed:
        assert r.status_code != 403, f"{method} {url} with [{scope}] should pass the scope gate"
    else:
        assert r.status_code == 403, f"{method} {url} with [{scope}] → {r.status_code}, expected 403"
        assert r.json()["detail"] == "Insufficient scope for this endpoint"


def test_browser_only_key_reaches_no_route_at_all():
    """The headline of rc.18 B2, stated as one fact: ``browser`` grants nothing at this edge.

    v0.12 serves no browser-tool surface through the gateway, so a browser-only key must be able to
    authenticate and do nothing else. RED before the fix on 7 routes — most consequentially
    ``POST /bots``, which really did schedule a container on staging.
    """
    client = _client(["browser"])
    for method, url, _template in CASES:
        assert _request(client, method, url).status_code == 403, f"{method} {url} let a browser key in"


def test_browser_only_key_can_still_read_its_own_identity():
    """The one thing it may do: find out what it is. /auth/me carries no scope BY DECLARATION
    (UNSCOPED_ROUTES), not by omission — otherwise a browser key could not discover its own limits."""
    r = _client(["browser"]).get("/auth/me", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["scopes"] == ["browser"]


def test_browser_only_key_never_reaches_the_downstream():
    """Fail-closed, not fail-late: the spawn is refused at the EDGE, so meeting-api is never asked
    to create anything. On staging the forward DID happen — a bot container was scheduled."""
    downstream = FakeDownstream(status_code=201, body={"id": 26362})
    app = create_app(
        FakeAuthorizer(user={"user_id": 7, "scopes": ["browser"], "max_concurrent": 3}),
        downstream, FakeRedis(),
    )
    r = TestClient(app).post("/bots", headers=AUTH,
                             json={"platform": "google_meet", "native_meeting_id": "lbc-jstz-znj"})
    assert r.status_code == 403
    assert downstream.last is None, "the under-scoped spawn must not reach meeting-api"


def test_bot_scope_still_runs_the_bot_lifecycle():
    """Negative control on the tightening: the scope the docs say owns bots still owns them, so
    this is a fix and not a lockout. A ``bot``-only key spawns and stops."""
    client = _client(["bot"])
    assert client.post("/bots", headers=AUTH,
                       json={"platform": "google_meet", "native_meeting_id": "abc"}).status_code == 200
    assert client.delete("/bots/google_meet/abc", headers=AUTH).status_code == 200
    assert client.get("/bots/status", headers=AUTH).status_code == 200


def test_a_bot_and_tx_key_reaches_every_route():
    """The shape every real key has (the terminal mints bot+tx+browser; the docs' own mint example
    is bot+tx) is unaffected end to end — no route in the matrix regresses to 403."""
    client = _client(["bot", "tx"])
    for method, url, _template in CASES:
        assert _request(client, method, url).status_code != 403, f"{method} {url} regressed"


# --- deny by default -----------------------------------------------------------------------------

def test_every_registered_route_declares_its_scopes():
    """Exhaustive over the ROUTER, not over a hand-kept list: any route on the app that declares no
    scope is the finding. This is the check that would have caught /user/webhook and /user/calendar
    when they were added."""
    app = create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis())
    assert undeclared_routes(app) == []


def test_the_guard_actually_catches_an_undeclared_route():
    """Negative control on the guard itself — a green check that cannot go red proves nothing.
    Register a new route on a built app and the guard must name it."""
    app = create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis())

    async def _new_endpoint():  # pragma: no cover - never called
        return {}

    app.router.routes.append(APIRoute("/user/quota", _new_endpoint, methods=["PUT"]))
    assert undeclared_routes(app) == [("PUT", "/user/quota")]



def _without(monkeypatch, method: str, path: str):
    """Build the app as if the OWNING DOMAIN had failed to declare one route.

    These two tests used to `del ROUTE_SCOPES[...]` — the table was a module-level literal and the
    app read it live. It is assembled from each domain's `routes.v1.json` now, so removing the row
    at its source is removing it from the assembly the app is built with. Same invariant, one layer
    out: the hole is a domain's missing declaration rather than an edit to the edge's own dict."""
    real = routes_manifest.load

    def _short(present, **kw):
        a = real(present, **kw)
        a.scopes.pop((method, path), None)
        a.unscoped.discard((method, path))
        return a

    monkeypatch.setattr(routes_manifest, "load", _short)


def test_an_undeclared_route_is_denied_at_request_time(monkeypatch):
    """The second wall, exercised through the real request path: with its declaration removed, a
    route refuses a FULL-scope key rather than forwarding it. So the failure mode of forgetting a
    declaration is a 403 the author trips over — never an open door."""
    assert TestClient(create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis())).get(
        "/bots/status", headers=AUTH).status_code == 200            # bot+tx+browser key

    _without(monkeypatch, "GET", "/bots/status")
    # The BUILD-time wall fires first and by design, so this app is constructed with the check
    # relaxed — the point here is the REQUEST path, which is the second wall behind it.
    monkeypatch.setattr(gateway_app, "undeclared_routes", lambda *a, **k: [])
    client = TestClient(create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis()))
    r = client.get("/bots/status", headers=AUTH)
    assert r.status_code == 403
    assert r.json()["detail"] == "Insufficient scope for this endpoint"


def test_an_undeclared_route_cannot_be_built(monkeypatch):
    """The first wall: create_app refuses to return an app whose router has an undeclared route.
    An under-declared gateway does not boot — the hole cannot reach an image."""
    _without(monkeypatch, "POST", "/bots")
    with pytest.raises(RuntimeError) as exc:
        create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis())
    assert "POST /bots" in str(exc.value)
    assert "routes.v1.json" in str(exc.value), "the refusal must name where the declaration goes"


def test_matrix_covers_every_declared_route():
    """The matrix above is exhaustive over ROUTE_SCOPES — no declaration goes unexercised, and no
    stale CASES row survives a route being removed."""
    covered = {(method, template) for method, _url, template in CASES}
    assert covered == set(ROUTE_SCOPES), (
        f"declared but unexercised: {sorted(set(ROUTE_SCOPES) - covered)}; "
        f"exercised but undeclared: {sorted(covered - set(ROUTE_SCOPES))}"
    )


def test_unscoped_routes_are_identity_only():
    """The escape hatch stays small and deliberate: only /health (the LB probe) and /auth/me (who
    am I) may carry no scope, and neither forwards anything downstream."""
    assert UNSCOPED_ROUTES == frozenset({("GET", "/health"), ("GET", "/auth/me")})


def test_scope_lookup_denies_when_the_matched_route_is_unknowable():
    """``_required_scopes`` is the single decision point, and its ``None`` means DENY. If Starlette
    ever stopped publishing the matched route, the edge must close, not open."""

    class _NoRoute:
        method = "GET"
        scope: dict = {}

    assert app_module._required_scopes(_NoRoute()) is None


def test_route_scope_declarations_only_use_real_scopes():
    """A typo'd scope name ('bots', 'transcript') would be unsatisfiable and lock a route out
    silently; the vocabulary is the one admin-api mints against."""
    vocabulary = {"bot", "tx", "browser"}
    for key, scopes in ROUTE_SCOPES.items():
        assert scopes, f"{key} declares an EMPTY scope set — that denies every key"
        assert set(scopes) <= vocabulary, f"{key} declares unknown scope(s) {set(scopes) - vocabulary}"


def test_app_is_a_fastapi_instance_after_the_build_guard():
    """Sanity: the build-time assertion runs AFTER every route is registered and returns the app."""
    assert isinstance(create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis()), FastAPI)
