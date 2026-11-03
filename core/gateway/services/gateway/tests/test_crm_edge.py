"""ADR-0047 + ADR-0036 one edge: the CRM record page's three verbs and the desktop's four are fronted at
the gateway under /crm/*, only where the deployment names CRM_API_URL, and forwarded with the caller's
own key."""
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from gateway import create_app, routes_manifest, undeclared_routes
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

CRM_ROWS = {("POST", "/crm/read"), ("POST", "/crm/history"), ("POST", "/crm/review")}
DESK_ROWS = {("POST", "/crm/desk/read"), ("POST", "/crm/desk/arrange"), ("POST", "/crm/desk/proposal"),
             ("POST", "/crm/desk/undo")}
SETTINGS_ROWS = {("POST", "/crm/desk/workspaces/read"), ("POST", "/crm/desk/workspaces/set")}
CARD_ROWS = {("POST", f"/crm/card/{verb}") for verb in ("list", "read", "create", "update", "members", "membership")}


def _registered(app):
    return {(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in (r.methods or ()) if m != "HEAD"}


def test_without_a_crm_the_edge_serves_no_crm_route():
    app = create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis(), crm_api_url="")
    assert _registered(app) & (CRM_ROWS | DESK_ROWS | SETTINGS_ROWS | CARD_ROWS) == set()
    client = TestClient(app)
    assert client.post("/crm/read", json={"record_id": "r"}, headers={"x-api-key": VALID_KEY}).status_code == 404


def test_with_a_crm_the_three_verbs_reach_its_own_paths_with_the_callers_key():
    down = FakeDownstream(body={"id": "r"})
    app = create_app(FakeAuthorizer(), down, FakeRedis(), crm_api_url="http://crm-api:8300")
    assert CRM_ROWS <= _registered(app)
    present = routes_manifest.load({"gateway", "meetings", "identity", "mcp", "agent", "crm"})
    assert undeclared_routes(app, present.scopes, present.unscoped) == []
    client = TestClient(app)
    r = client.post("/crm/read", json={"record_id": "r"}, headers={"x-api-key": VALID_KEY})
    assert r.status_code == 200 and r.json() == {"id": "r"}
    assert down.last["url"] == "http://crm-api:8300/read"
    assert down.last["headers"]["x-api-key"] == VALID_KEY
    assert client.post("/crm/read", json={}).status_code == 401              # no key, no forward
    assert client.post("/crm/change", json={}, headers={"x-api-key": VALID_KEY}).status_code == 404


def test_the_desktops_four_verbs_reach_crm_apis_desk_paths_with_the_callers_key():
    down = FakeDownstream(body={"version": 0})
    app = create_app(FakeAuthorizer(), down, FakeRedis(), crm_api_url="http://crm-api:8300")
    assert DESK_ROWS <= _registered(app)
    client = TestClient(app)
    for verb in ("read", "arrange", "proposal", "undo"):
        r = client.post(f"/crm/desk/{verb}", json={}, headers={"x-api-key": VALID_KEY})
        assert r.status_code == 200
        assert down.last["url"] == f"http://crm-api:8300/desk/{verb}"
        assert down.last["headers"]["x-api-key"] == VALID_KEY
    assert client.post("/crm/desk/read", json={}).status_code == 401
    # the propose route is the agent's (desktop_arrange_propose, through the MCP), not the terminal's
    assert client.post("/crm/desk/arrange/propose", json={}, headers={"x-api-key": VALID_KEY}).status_code == 404


def test_the_persons_workspace_settings_reach_crm_apis_desk_paths_with_the_callers_key():
    """Per-person workspace settings live on the CRM member_of relation; the terminal reads and sets
    them with the person's own key, under the desk's prefix (the `desktop` switch)."""
    down = FakeDownstream(body={"workspaces": {}})
    app = create_app(FakeAuthorizer(), down, FakeRedis(), crm_api_url="http://crm-api:8300")
    assert SETTINGS_ROWS <= _registered(app)
    present = routes_manifest.load({"gateway", "meetings", "identity", "mcp", "agent", "crm"})
    assert undeclared_routes(app, present.scopes, present.unscoped) == []
    client = TestClient(app)
    for verb in ("read", "set"):
        r = client.post(f"/crm/desk/workspaces/{verb}", json={}, headers={"x-api-key": VALID_KEY})
        assert r.status_code == 200
        assert down.last["url"] == f"http://crm-api:8300/desk/workspaces/{verb}"
        assert down.last["headers"]["x-api-key"] == VALID_KEY
    assert client.post("/crm/desk/workspaces/set", json={}).status_code == 401


def test_the_cards_person_verbs_reach_crm_apis_card_paths_and_the_proposals_stay_the_agents():
    """Desktop D2: the terminal's card verbs are a person's, with their own key; an agent proposes
    only through card_propose / card_update_propose in the MCP, never through the edge."""
    down = FakeDownstream(body={"cards": [], "proposals": []})
    app = create_app(FakeAuthorizer(), down, FakeRedis(), crm_api_url="http://crm-api:8300")
    assert CARD_ROWS <= _registered(app)
    client = TestClient(app)
    for verb in ("list", "read", "create", "update", "members", "membership"):
        r = client.post(f"/crm/card/{verb}", json={}, headers={"x-api-key": VALID_KEY})
        assert r.status_code == 200
        assert down.last["url"] == f"http://crm-api:8300/card/{verb}"
        assert down.last["headers"]["x-api-key"] == VALID_KEY
    assert client.post("/crm/card/create", json={}).status_code == 401
    for agents_only in ("/crm/card/propose", "/crm/card/update/propose"):
        assert client.post(agents_only, json={}, headers={"x-api-key": VALID_KEY}).status_code == 404
