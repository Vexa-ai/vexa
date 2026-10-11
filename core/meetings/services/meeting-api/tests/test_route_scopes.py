"""route_scopes — the edge's scopes, checked again on the route a request matched.

The gateway checks a key's scopes against the meetings rows (`core/meetings/routes.v1.json`) and
signs them onto the hop. meeting-api checks them again against the route the request MATCHED, from
the same rows: a hop that landed on a route other than the one the edge checked is refused by the
route it landed on. A signed identity on a route no row reaches is refused outright; the internal
tier and the self-authenticating callbacks are not keys and are not checked here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app, identity_token, route_scopes
from meeting_api.route_scopes import INSUFFICIENT_SCOPE, ROUTE_SCOPES, ScopeTableError, scopes_by_route

KEY = identity_token.generate_signing_key()
INTERNAL = "route-scopes-internal-secret"
MANIFEST = json.loads((Path(__file__).resolve().parents[3] / "routes.v1.json").read_text())
_VALUES = {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "meeting_id": "1",
           "recording_id": "1", "media_file_id": "1", "calendar_id": "work-1"}
#: Rows the edge declares whose meeting-api route does not exist yet (api.v1 known gaps): the hop
#: answers 404 and no scope is read. Held exactly, so a row that loses its route is noticed.
_KNOWN_UNSERVED = {("POST", "/bots/{platform}/{native_meeting_id}/speak")}


@pytest.fixture
def client():
    return TestClient(create_app(open_callbacks=True, identity_key=KEY.public_key(),
                                 internal_secret=INTERNAL))


def _signed(scopes, sub: str = "7") -> dict:
    return {identity_token.HEADER: identity_token.sign(
        KEY, {"sub": sub, "scopes": list(scopes), "limits": 3})}


def _concrete(template: str) -> str:
    for name, value in _VALUES.items():
        template = template.replace("{" + name + "}", value)
    return template


def _served(app) -> set:
    def walk(routes):
        out = set()
        for r in routes:
            if getattr(r, "include_context", None) is not None:
                out |= walk(r.original_router.routes)
            elif getattr(r, "path", None) and getattr(r, "methods", None):
                out |= {(m, r.path) for m in r.methods}
        return out
    return walk(app.routes)


def _scope_refused(r) -> bool:
    return r.status_code == 403 and r.json().get("detail") == INSUFFICIENT_SCOPE


# ── the table is the manifest ────────────────────────────────────────────────────────────────────

def test_the_table_is_read_from_the_meetings_manifest():
    assert ROUTE_SCOPES == scopes_by_route(MANIFEST)
    assert ROUTE_SCOPES[("POST", "/bots")] == {"bot"}
    assert ROUTE_SCOPES[("GET", "/meetings")] == {"tx"}
    # An alias row reaches the route its `upstream` names, with its own scopes.
    assert ROUTE_SCOPES[("POST", "/meetings/{meeting_id}/share")] == {"tx"}
    assert ROUTE_SCOPES[("GET", "/webhooks/deliveries")] == {"bot", "tx"}
    assert ("GET", "/user/webhook/deliveries") not in ROUTE_SCOPES


def test_every_row_reaches_a_route_this_app_serves_except_the_known_gaps():
    served = _served(create_app())
    unserved = {k for k in ROUTE_SCOPES if k not in served}
    assert unserved == _KNOWN_UNSERVED


@pytest.mark.parametrize("doc", [
    {**MANIFEST, "domain": "agent"},
    {**MANIFEST, "contract": "routes.v2"},
    {**MANIFEST, "routes": []},
    {**MANIFEST, "routes": [{"method": "GET", "path": "/meetings", "scopes": []}]},
    {**MANIFEST, "routes": [{"method": "GET", "path": "meetings", "scopes": ["tx"]}]},
    {**MANIFEST, "routes": ["GET /meetings"]},
])
def test_a_manifest_this_service_cannot_read_as_a_table_refuses_to_boot(doc):
    with pytest.raises(ScopeTableError):
        scopes_by_route(doc)


# ── the check ────────────────────────────────────────────────────────────────────────────────────

_CHECKED = sorted(k for k in ROUTE_SCOPES if k not in _KNOWN_UNSERVED)


@pytest.mark.parametrize("method,template", _CHECKED)
def test_a_key_without_the_route_s_scope_is_refused(client, method, template):
    """Generated over every route a row reaches: a signed identity holding only scopes the route
    does not take is refused here, whatever the edge decided."""
    others = {"bot", "tx", "browser"} - ROUTE_SCOPES[(method, template)]
    r = client.request(method, _concrete(template), headers=_signed(others or ["browser"]), json={})
    assert _scope_refused(r), (method, template, r.status_code, r.text[:200])


@pytest.mark.parametrize("method,template", _CHECKED)
def test_a_key_with_the_route_s_scope_passes_the_check(client, method, template):
    scope = sorted(ROUTE_SCOPES[(method, template)])[0]
    r = client.request(method, _concrete(template), headers=_signed([scope]), json={})
    assert not _scope_refused(r), (method, template, r.status_code, r.text[:200])


def test_a_tx_key_that_reached_the_bot_spawn_route_is_refused_and_nothing_is_spawned(client):
    """The cross-scope hop: a `tx` key whose request arrived at `POST /bots` (scope `bot`) — the
    shape a mis-routed annotate would take — is refused before the route runs."""
    r = client.post("/bots", headers=_signed(["tx"]),
                    json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert _scope_refused(r)
    listing = client.get("/bots", headers=_signed(["bot"]))
    assert listing.status_code == 200
    assert "abc-defg-hij" not in listing.text


def test_a_bot_key_that_reached_a_meeting_record_route_is_refused(client):
    assert _scope_refused(client.get("/meetings", headers=_signed(["bot"])))
    assert _scope_refused(client.post("/meetings", headers=_signed(["bot"]), json={}))


@pytest.mark.parametrize("method,path", [
    ("POST", "/meetings/google_meet/abc-defg-hij/docs"),
    ("DELETE", "/meetings/google_meet/abc-defg-hij/docs"),
    ("POST", "/internal/recordings/upload"),
    ("POST", "/bots/internal/callback/lifecycle"),
    ("POST", "/runtime/callback"),
])
def test_a_signed_identity_on_a_route_no_row_reaches_is_refused(client, method, path):
    """Deny by default: the edge forwards a signed identity only along its rows (and its own `/ws`
    hop), so one anywhere else did not come from a row the edge checked."""
    assert _scope_refused(client.request(method, path, headers=_signed(["bot", "tx"]), json={}))


@pytest.mark.parametrize("scopes", [["browser"], ["bot"], ["bot", "browser"]])
def test_the_edge_s_subscribe_hop_refuses_a_key_that_may_not_read_transcripts(client, scopes):
    """`/ws` subscribing opens a meeting's live transcript: the hop takes the transcript read's
    scopes, as the gateway's socket does before it asks (R6-9)."""
    r = client.post("/ws/authorize-subscribe", headers=_signed(scopes),
                    json={"meetings": [{"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}]})
    assert _scope_refused(r), r.text


def test_the_edge_s_subscribe_hop_takes_a_transcript_key(client):
    r = client.post("/ws/authorize-subscribe", headers=_signed(["tx"]),
                    json={"meetings": [{"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}]})
    assert not _scope_refused(r), r.text


def test_the_internal_tier_is_not_a_key_and_is_not_checked(client):
    r = client.get("/meetings", headers={"X-Internal-Secret": INTERNAL, "X-User-Id": "7"})
    assert r.status_code == 200, r.text


def test_an_unsigned_callback_is_not_checked(client):
    r = client.post("/bots/internal/callback/lifecycle", json={})
    assert not _scope_refused(r)


def test_the_check_runs_without_the_identity_door_only_on_a_signed_header():
    """The in-process harness has no door: a request with no signed identity is not checked."""
    c = TestClient(create_app())
    assert not _scope_refused(c.get("/meetings", headers={"X-User-Id": "7"}))


def test_the_image_carries_the_manifest_where_this_module_reads_it():
    """meeting-api's image puts `/app/meetings/routes.v1.json` beside the sealed contracts this
    package reads by walking up from its own file."""
    root = Path(__file__).resolve().parents[5]
    dockerfile = (root / "core/meetings/services/meeting-api/Dockerfile").read_text()
    assert "COPY core/meetings/routes.v1.json" in dockerfile
    assert "./meetings/routes.v1.json" in dockerfile
    assert route_scopes.MANIFEST == Path("meetings") / "routes.v1.json"
