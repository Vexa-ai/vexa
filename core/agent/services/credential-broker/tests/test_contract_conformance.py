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

from conftest import CONTRACT, golden, schema
from credential_broker import app as app_module, providers, secret_service

SCHEMA = schema()
ROUTES = SCHEMA["x-routes"]
CID = "0123456789abcdef0123456789abcdef"
MODELS = {
    "SetupRequest": app_module.SetupBody, "PrepareRequest": app_module.PreparedSetupBody,
    "CustomSecretRequest": app_module.CustomSecretBody, "OAuthApplicationRequest": app_module.OAuthApplicationBody,
    "AccountReadRequest": app_module.AccountReadBody, "GmailDraftRequest": app_module.GmailDraftBody,
    "CustomCallRequest": app_module.CustomCallBody, "GitSecretRequest": app_module.GitSecretBody,
}


def conforms(shape, data):
    jsonschema.validate(data, {"$ref": f"#/$defs/{shape}", **{k: v for k, v in SCHEMA.items() if k != "x-routes"}})


def test_registered_routes_equal_the_contract(client):
    served = {(m, r.path.replace("{cid}", "{cid}")) for r in client.app.routes if isinstance(r, APIRoute) for m in r.methods}
    declared = {(r["method"], r["path"]) for r in ROUTES}
    assert served == declared


@pytest.mark.parametrize("route", [r for r in ROUTES if r["roles"]], ids=lambda r: r["method"] + " " + r["path"])
def test_each_route_refuses_roles_it_does_not_grant(signed, route):
    path = route["path"].replace("{cid}", CID)
    body = {} if route["method"] == "POST" else None
    for role in {"agent", "human", "git"} - set(route["roles"]):
        r = signed(role, route["method"], path, body, actor="2")
        assert r.status_code in (403, 422), (role, r.status_code)
        if r.status_code == 422:      # body validation runs first; with a valid body it is still 403
            assert route.get("request")


@pytest.mark.parametrize("name", sorted(p.name for p in (CONTRACT / "golden").glob("*Request.*.json")))
def test_request_goldens_are_accepted_by_the_route_models(name):
    shape = name.split(".")[0]
    MODELS[shape].model_validate(golden(name))


def test_responses_conform(signed, connection, ready, store, client):
    conforms("Health", client.get("/health").json())
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
    store.put(gmail, {"value": {"access_token": "t", "expires_at": 9e12, "scope": providers.DRAFT_SCOPE}})
    ready(gmail)
    with patch.object(providers, "read_account", return_value={"messages": [], "has_more": False, "next_page_token": ""}):
        conforms("AccountReadResponse", signed("agent", "POST", f"/api/connections/{gmail}/read", golden("AccountReadRequest.gmail-search.json")).json())
    with patch.object(providers, "create_gmail_draft", return_value=golden("GmailDraftResponse.created.json")):
        conforms("GmailDraftResponse", signed("agent", "POST", f"/api/connections/{gmail}/draft", golden("GmailDraftRequest.reply.json")).json())
    git = signed("git", "POST", "/api/internal/git-secret", golden("GitSecretRequest.put.json"), actor="42")
    conforms("GitSecretResponse", git.json())
    refused = signed("human", "GET", "/api/connections", at=1)
    conforms("Error", refused.json())
