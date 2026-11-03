"""gate:contract-conformance — the shipped broker serves exactly credential-broker.v1.

Both directions: every route the app registers is in `x-routes`, and every `x-routes` entry is
registered; each route refuses every role the contract does not grant it; every request golden is
accepted by the route's model; and the responses the routes actually return conform to their
response shape.
"""
import json
from unittest.mock import patch

import jsonschema
import pytest
from fastapi.routing import APIRoute

from conftest import CONTRACT, golden, schema, OWNER
from credential_broker import models, providers, secret_service

SCHEMA = schema()
ROUTES = SCHEMA["x-routes"]
CID = "0123456789abcdef0123456789abcdef"
MODELS = {
    "SetupRequest": models.SetupBody, "PrepareRequest": models.PreparedSetupBody,
    "CustomSecretRequest": models.CustomSecretBody, "OAuthApplicationRequest": models.OAuthApplicationBody,
    "AccountReadRequest": models.AccountReadBody, "GmailDraftRequest": models.GmailDraftBody,
    "CustomCallRequest": models.CustomCallBody, "GitSecretRequest": models.GitSecretBody,
    "GmailDraftUpdateRequest": models.GmailDraftUpdateBody, "CalendarEventRequest": models.CalendarEventBody,
    "AuthorizeRequest": models.AuthorizeBody,
    "BotAccountRequest": models.BotAccountBody,
    "CalendarChangesRequest": models.CalendarChangesBody,
    "SensorGrantRequest": models.SensorGrantBody,
    "SignalsModelKeyRequest": models.SignalsModelKeyBody,
    "MailCredentialRequest": models.MailCredentialBody,
}


def conforms(shape, data):
    jsonschema.validate(data, {"$ref": f"#/$defs/{shape}", **{k: v for k, v in SCHEMA.items() if k != "x-routes"}})


def served_routes(routes, prefix=""):
    """Every route a request can reach. FastAPI keeps an included router as one placeholder in
    `app.routes` (its routes resolve at match time), so the placeholder is opened here; reading
    `app.routes` alone would find no connection routes and test nothing."""
    out = []
    for r in routes:
        inc = getattr(r, "include_context", None)
        if inc is not None:
            out.extend(served_routes(r.original_router.routes, prefix + (getattr(inc, "prefix", "") or "")))
        elif isinstance(r, APIRoute):
            out.extend((m, prefix + r.path) for m in r.methods)
    return out


#: Routes the service serves that credential-broker.v1 does not list yet, each awaiting the contract
#: change that adds it. Self-closing: once the contract lists one, the test below fails until it is
#: removed from here.
AWAITING_CONTRACT: set = set()


def test_registered_routes_equal_the_contract(client):
    served = set(served_routes(client.app.routes))
    assert len(served) == len(served_routes(client.app.routes))      # no route registered twice
    declared = {(r["method"], r["path"]) for r in ROUTES}
    assert not AWAITING_CONTRACT & declared, "the contract now lists it: drop it from AWAITING_CONTRACT"
    assert served == declared | AWAITING_CONTRACT


@pytest.mark.parametrize("route", [r for r in ROUTES if r["roles"]], ids=lambda r: r["method"] + " " + r["path"])
def test_each_route_refuses_roles_it_does_not_grant(signed, route):
    path = route["path"].replace("{cid}", CID)
    body = {} if route["method"] == "POST" else None
    for role in {"agent", "human", "git", "bots"} - set(route["roles"]):
        r = signed(role, route["method"], path, body, actor="2")
        assert r.status_code in (403, 422), (role, r.status_code)
        if r.status_code == 422:      # body validation runs first; with a valid body it is still 403
            assert route.get("request")


@pytest.mark.parametrize("name", sorted(p.name for p in (CONTRACT / "golden").glob("*Request.*.json")))
def test_request_goldens_are_accepted_by_the_route_models(name):
    shape = name.split(".")[0]
    MODELS[shape].model_validate(golden(name))


def test_responses_conform(signed, connection, ready, store, client, broker):
    conforms("Health", client.get("/health").json())
    conforms("Readiness", client.get("/ready").json())
    r = signed("agent", "POST", "/api/setup", golden("SetupRequest.custom-secret.json"))
    conforms("ConnectionState", r.json())
    cid = r.json()["connection_id"]
    conforms("ConnectionState", signed("agent", "POST", f"/api/connections/{cid}/request").json())
    prepared = signed("agent", "POST", f"/api/connections/{cid}/prepare", golden("PrepareRequest.telegram.json"))
    conforms("PrepareResponse", prepared.json())
    rows = signed("agent", "GET", "/api/connections").json()
    conforms("ConnectionList", rows)
    saved = signed("human", "POST", f"/api/connections/{cid}/custom-secret",
                   {"value": "123:fixture_token", "fields": {"chat_id": "-1"}, "confirmed_host": "api.telegram.org",
                    "setup_request": rows["connections"][0]["setup_request"]})
    conforms("ConnectionState", saved.json())
    with patch.object(secret_service, "execute", return_value={"http_status": 200, "content": {"ok": True}, "untrusted_content": True}):
        conforms("CustomCallResponse", signed("agent", "POST", f"/api/connections/{cid}/call", golden("CustomCallRequest.send.json")).json())
    conforms("ConnectionState", signed("human", "POST", f"/api/connections/{cid}/disconnect").json())
    conforms("ConnectionState", signed("human", "POST", f"/api/connections/{cid}/delete").json())
    gmail = connection("google_email")
    conforms("AuthorizeResponse", signed("human", "POST", f"/api/connections/{gmail}/authorize").json())
    store.put(gmail, {"owner": OWNER, "value": {"access_token": "t", "expires_at": 9e12, "scope": providers.DRAFT_SCOPE}})
    ready(gmail)
    with patch.object(providers, "read_account", return_value={"messages": [], "has_more": False, "next_page_token": ""}):
        conforms("AccountReadResponse", signed("agent", "POST", f"/api/connections/{gmail}/read", golden("AccountReadRequest.gmail-search.json")).json())
    with patch.object(providers, "create_gmail_draft", return_value=golden("GmailDraftResponse.created.json")):
        conforms("GmailDraftResponse", signed("agent", "POST", f"/api/connections/{gmail}/draft", golden("GmailDraftRequest.reply.json")).json())
    broker.sql("INSERT INTO drafts VALUES (?,?,?,?)", ("draft", OWNER, gmail, 0))
    with patch.object(providers, "create_gmail_draft", return_value=golden("GmailDraftResponse.updated.json")):
        conforms("GmailDraftResponse", signed("agent", "POST", f"/api/connections/{gmail}/draft-update",
                                               {**golden("GmailDraftUpdateRequest.update.json"), "draft_id": "draft"}).json())
    calendar = connection("google_calendar")
    conforms("AuthorizeResponse", signed("human", "POST", f"/api/connections/{calendar}/authorize",
                                         golden("AuthorizeRequest.calendar-write.json")).json())
    store.put(calendar, {"owner": OWNER, "value": {"access_token": "t", "expires_at": 9e12, "scope": ""}})
    ready(calendar)
    conforms("CalendarEventResponse", signed("agent", "POST", f"/api/connections/{calendar}/calendar-event",
                                             golden("CalendarEventRequest.create.json")).json())
    store.put(calendar, {"owner": OWNER, "value": {"access_token": "t", "expires_at": 9e12,
                                                   "scope": providers.CALENDAR_WRITE_SCOPE}})
    ready(calendar, version=2)
    with patch.object(providers, "write_calendar_event", return_value=golden("CalendarEventResponse.created.json")):
        conforms("CalendarEventResponse", signed("agent", "POST", f"/api/connections/{calendar}/calendar-event",
                                                 golden("CalendarEventRequest.create.json")).json())
    conforms("ActionPreferences", signed("agent", "POST", "/api/preferences", golden("ActionPreferences.narrowed.json")).json())
    conforms("ActionPreferences", signed("agent", "GET", "/api/preferences").json())
    conforms("ConnectionList", signed("agent", "GET", "/api/connections").json())
    git = signed("git", "POST", "/api/internal/git-secret", golden("GitSecretRequest.put.json"), actor="42")
    conforms("GitSecretResponse", git.json())
    bot = golden("BotAccountRequest.put.json")
    conforms("BotAccountResponse", signed("bots", "POST", "/api/internal/bot-account", bot, actor="meeting-api").json())
    listed = signed("bots", "POST", "/api/internal/bot-account", golden("BotAccountRequest.list.json"), actor="meeting-api")
    conforms("BotAccountResponse", listed.json())
    got = signed("bots", "POST", "/api/internal/bot-account", {"action": "get", "id": bot["id"]}, actor="meeting-api")
    conforms("BotAccountResponse", got.json())
    gone = signed("bots", "POST", "/api/internal/bot-account", {"action": "delete", "id": bot["id"]}, actor="meeting-api")
    conforms("BotAccountResponse", gone.json())
    refused = signed("human", "GET", "/api/connections", at=1)
    conforms("Error", refused.json())
