"""The service door: what the flows tier's credential can and cannot reach.

The flows services used to hold `INTERNAL_API_SECRET` (every `/internal/*` and `/api/*` route of every
domain, as any `X-User-Id`) and the admin key (any person's gateway token, minted on request). They
now hold one key that opens this door, and the door forwards only the calls on its allow-list. These
tests pin the refusals first — they are the security claim — and then that every call flows makes
still crosses, carrying what its route needs and nothing it does not.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import identity_token
from gateway.service_door import (FLOWS_INBOUND, FLOWS_OUTBOUND, build_door_app, create_door_app,
                                  usable_key)

FLOWS_KEY = "flows-key-0123456789abcdef0123456789abcdef"
INTERNAL = "internal-0123456789abcdef0123456789abcdef"
SIGNING_KEY = identity_token.generate_signing_key()
UPSTREAMS = {"agent": "http://agent-api:8100", "identity": "http://admin-api:8001",
             "meetings": "http://meeting-api:8080", "crm": "http://crm-api:8200"}
IDENTITY_OF_7 = {"user_id": 7, "scopes": [], "max_concurrent": 2, "email": "ann@acme.test",
                 "is_admin": False, "admin_account": False, "workspaces": ["ws-1"],
                 "writable_workspaces": ["ws-1"]}


class Upstream:
    """Records every request the door sends; answers identity lookups for subject 7 only."""

    def __init__(self):
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.url.path == "/internal/users/7/identity":
            return httpx.Response(200, json=IDENTITY_OF_7)
        if request.url.path.endswith("/identity"):
            return httpx.Response(404, json={"detail": "Unknown user"})
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    def forwarded(self) -> list[httpx.Request]:
        return [r for r in self.seen if not r.url.path.endswith("/identity")]


@pytest.fixture()
def door():
    up = Upstream()
    app = create_door_app(flows_key=FLOWS_KEY, internal_secret=INTERNAL, upstreams=UPSTREAMS,
                          flows_api_url="http://flows-api:8090",
                          client=httpx.AsyncClient(transport=httpx.MockTransport(up)),
                          identity_key=SIGNING_KEY)
    with TestClient(app) as c:
        yield c, up


def _flows(**extra) -> dict:
    return {"X-Vexa-Service-Key": FLOWS_KEY, **extra}


# ── refusals: the claim ─────────────────────────────────────────────────────────────────────────

def test_the_flows_credential_cannot_obtain_a_token_for_anybody(door):
    """The admin-api door that minted a person's gateway token, and every other admin route, is not
    on the list — whatever the subject, the body or the spelling."""
    c, up = door
    for method, path in [("POST", "/identity/admin/users/7/tokens"),
                         ("POST", "/identity/admin/users/8/tokens"),
                         ("GET", "/identity/admin/users/7"),
                         ("POST", "/identity/admin/users"),
                         ("GET", "/identity/admin/users/email/ann@acme.test"),
                         ("POST", "/identity/internal/bootstrap-admin"),
                         ("POST", "/identity/internal/release-admin"),
                         ("PUT", "/identity/internal/users/7/settings")]:
        r = c.request(method, path, headers=_flows(**{"X-User-Id": "7"}), json={"scopes": ["bot"]})
        assert r.status_code == 403, (method, path, r.status_code)
        assert r.json()["reason"] == "not_on_the_allow_list"
    assert up.seen == [], "a refused call must never leave the door"


def test_the_flows_credential_cannot_read_a_calendar(door):
    """A person's calendar and mail are reached only through the person's own connection — flows
    has no route to them at all: not agent-api's connections, not the gateway's /agent rows, not the
    calendar sync, under any subject."""
    c, up = door
    for method, path in [("POST", "/agent/api/connections/calendar/events"),
                         ("GET", "/agent/api/connections/calendar/events"),
                         ("POST", "/agent/api/connections/gmail/search"),
                         ("POST", "/agent/api/connections/service/call"),
                         ("GET", "/agent/agent/connections/calendar/events"),
                         ("GET", "/meetings/user/calendar"),
                         ("POST", "/meetings/user/calendar/sync")]:
        r = c.request(method, path, headers=_flows(**{"X-User-Id": "7"}))
        assert r.status_code == 403, (method, path, r.status_code)
    assert up.seen == []


@pytest.mark.parametrize("method,path", [
    ("POST", "/agent/invocations"),                       # dispatch any unit as anyone
    ("GET", "/agent/api/admin/overview"),
    ("POST", "/agent/internal/policy/decide"),
    ("POST", "/agent/internal/invites/signin-claim"),
    ("DELETE", "/agent/api/workspace/file"),
    ("POST", "/agent/api/workspace/delete"),
    ("POST", "/agent/api/workspace/invite"),
    ("GET", "/agent/api/notifications"),
    ("POST", "/meetings/meetings/import"),
    ("GET", "/meetings/recordings"),
    ("DELETE", "/meetings/recordings/3"),
    ("PUT", "/meetings/bots/zoom/123/config"),
    ("POST", "/meetings/bots/zoom/123/speak"),
    ("GET", "/meetings/meetings/3/export"),
    ("POST", "/crm/internal/orgs/p-7/erase"),
    ("POST", "/crm/internal/persons"),
    ("GET", "/broker/api/internal/sensor/calendar-changes"),
])
def test_routes_off_the_list_are_refused(door, method, path):
    c, up = door
    r = c.request(method, path, headers=_flows(**{"X-User-Id": "7"}))
    assert r.status_code in (403, 404), r.status_code
    assert up.seen == []


def test_a_path_cannot_climb_out_of_a_listed_route(door):
    c, up = door
    for path in ["/agent/api/workspace/file/../../internal/policy/decide",
                 "/agent/api/workspace/%2e%2e/connections/calendar/events",
                 "/agent/api/sessions/x%2F..%2F..%2Fadmin%2Foverview/history",
                 "/meetings/meetings/3%2Fexport/share"]:
        r = c.get(path, headers=_flows(**{"X-User-Id": "7"}))
        assert r.status_code in (400, 403, 404), (path, r.status_code)
    assert up.forwarded() == []


def test_no_key_a_wrong_key_or_the_internal_secret_opens_nothing(door):
    c, up = door
    for headers in [{}, {"X-Vexa-Service-Key": "nope"}, {"X-Internal-Secret": INTERNAL},
                    {"X-Vexa-Service-Key": INTERNAL}]:
        r = c.get("/agent/api/workspace/file", headers={**headers, "X-User-Id": "7"},
                  params={"path": "x"})
        assert r.status_code == 403, (headers, r.status_code)
        assert r.json()["reason"] == "service_key_required"
    assert up.seen == []


def test_the_flows_key_does_not_open_the_inbound_leg_and_the_secret_does_not_open_outbound(door):
    c, up = door
    r = c.post("/to-flows/events/signal", headers=_flows(), json={})
    assert r.status_code == 403 and r.json()["reason"] == "internal_secret_required"
    r = c.post("/to-flows/flows/anything", headers={"X-Internal-Secret": INTERNAL}, json={})
    assert r.status_code == 403
    assert up.seen == []


def test_a_meetings_call_needs_a_real_subject(door):
    c, up = door
    assert c.get("/meetings/meetings", headers=_flows()).status_code == 400
    assert c.get("/meetings/meetings", headers=_flows(**{"X-User-Id": "flows"})).status_code == 400
    r = c.get("/meetings/meetings", headers=_flows(**{"X-User-Id": "8"}))   # identity: unknown
    assert r.status_code == 403 and r.json()["reason"] == "subject_unknown"
    assert up.forwarded() == []


def test_only_the_subject_crosses_never_a_scope_a_class_or_a_delegation(door):
    """Before, the internal tier believed every `x-user-*` a secret holder sent — scopes, the
    credential class (`session` satisfies session-only rules), workspaces, a delegation."""
    c, up = door
    forged = {"X-User-Scopes": "admin", "X-User-Credential-Class": "session",
              "X-User-Workspaces": "ws-9", "X-User-Writable-Workspaces": "ws-9",
              "X-User-Regime": "attended", "X-User-Delegation-Grants": "bot.request",
              "X-Vexa-Identity": "forged", "X-Admin-API-Key": "x", "X-Internal-Secret": "x",
              "Authorization": "Bearer x", "X-API-Key": "x"}
    c.get("/agent/api/workspace/file", params={"path": "a"},
          headers=_flows(**{"X-User-Id": "7", **forged}))
    c.get("/meetings/meetings", headers=_flows(**{"X-User-Id": "7", **forged}))
    agent, meetings = up.forwarded()
    for req in (agent, meetings):
        h = {k.lower(): v for k, v in req.headers.items()}
        assert "x-vexa-service-key" not in h
        assert "authorization" not in h and "x-api-key" not in h and "x-admin-api-key" not in h
        assert h.get("x-user-credential-class") is None
        assert h.get("x-user-regime") is None and h.get("x-user-delegation-grants") is None
        assert h.get("x-user-scopes") in (None, "bot,tx")
        assert "ws-9" not in json.dumps(h)
    assert {k.lower(): v for k, v in agent.headers.items()}["x-internal-secret"] == INTERNAL


# ── what still crosses: every call flows makes ──────────────────────────────────────────────────

def _concrete(template: str) -> str:
    return template.replace("{}", "42")


@pytest.mark.parametrize("domain,method,template",
                         [(d, m, t) for d, rows in FLOWS_OUTBOUND.items() for m, t in rows])
def test_every_listed_call_crosses_to_its_own_route(door, domain, method, template):
    c, up = door
    path = _concrete(template)
    r = c.request(method, f"/{domain}{path}", params={"q": "1"},
                  headers=_flows(**{"X-User-Id": "7", "X-Vexa-Flow-Regime": "autonomous"}),
                  json={"a": 1} if method in ("POST", "PUT") else None)
    assert r.status_code == 200, r.text
    (sent,) = up.forwarded()
    assert sent.method == method
    assert str(sent.url) == f"{UPSTREAMS[domain]}{path}?q=1"
    if method in ("POST", "PUT"):
        assert json.loads(sent.content) == {"a": 1}


def test_an_internal_tier_call_carries_the_secret_the_subject_and_the_flow_headers(door):
    c, up = door
    c.post("/agent/api/chat/submit", json={"m": 1}, headers=_flows(**{
        "X-User-Id": "7", "X-Vexa-Flow-Regime": "autonomous", "X-Vexa-Flow-Grants": "bot.request",
        "X-Vexa-Message-From-Uid": "9", "X-Trace-Id": "t-1"}))
    (sent,) = up.forwarded()
    h = {k.lower(): v for k, v in sent.headers.items()}
    assert h["x-internal-secret"] == INTERNAL and h["x-user-id"] == "7"
    assert h["x-vexa-flow-regime"] == "autonomous" and h["x-vexa-flow-grants"] == "bot.request"
    assert h["x-vexa-message-from-uid"] == "9" and h["x-trace-id"] == "t-1"


def test_a_meetings_call_carries_the_subject_s_signed_identity_with_the_door_s_scopes(door):
    """meeting-api checks a door call the way it checks the person's own key: verified identity,
    scopes, the bot limit, the workspaces they may write. No internal secret reaches it."""
    c, up = door
    c.post("/meetings/bots", json={"platform": "zoom"}, headers=_flows(**{"X-User-Id": "7"}))
    (sent,) = up.forwarded()
    h = {k.lower(): v for k, v in sent.headers.items()}
    assert "x-internal-secret" not in h
    claims = identity_token.verify(SIGNING_KEY.public_key(), h["x-vexa-identity"])
    assert claims["sub"] == "7" and claims["scopes"] == ["bot", "tx"]
    assert claims["limits"] == 2 and claims["writable_workspaces"] == ["ws-1"]
    assert "credential_class" not in claims


@pytest.mark.parametrize("method,path", FLOWS_INBOUND)
def test_the_inbound_leg_forwards_with_the_flows_key_never_the_secret(door, method, path):
    c, up = door
    r = c.request(method, f"/to-flows{path}", json={"e": 1},
                  headers={"X-Internal-Secret": INTERNAL, "X-Vexa-Service": "signals",
                           "X-User-Id": "7"})
    assert r.status_code == 200
    (sent,) = up.seen
    h = {k.lower(): v for k, v in sent.headers.items()}
    assert str(sent.url) == f"http://flows-api:8090{path}"
    assert h["x-vexa-service-key"] == FLOWS_KEY and h["x-vexa-service"] == "signals"
    assert "x-internal-secret" not in h and "x-user-id" not in h


def test_an_undeployed_domain_is_a_404_not_a_forward():
    up = Upstream()
    app = create_door_app(flows_key=FLOWS_KEY, internal_secret=INTERNAL,
                          upstreams={**UPSTREAMS, "crm": ""},
                          client=httpx.AsyncClient(transport=httpx.MockTransport(up)))
    with TestClient(app) as c:
        r = c.post("/crm/internal/references", headers=_flows(), json={})
    assert r.status_code == 404 and r.json()["reason"] == "domain_not_deployed"
    assert up.seen == []


def test_the_door_does_not_open_without_a_real_key():
    assert build_door_app({}) is None
    for bad in ("changeme", "secret", "vexa-internal-secret"):
        assert usable_key(bad) == ""
        with pytest.raises(RuntimeError):
            build_door_app({"VEXA_FLOWS_SERVICE_KEY": bad, "INTERNAL_API_SECRET": INTERNAL})
    with pytest.raises(RuntimeError):
        build_door_app({"VEXA_FLOWS_SERVICE_KEY": FLOWS_KEY})          # no internal secret
