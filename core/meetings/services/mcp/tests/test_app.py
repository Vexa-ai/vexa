"""L2/L3 — the shipped MCP service app against a fake gateway (httpx.MockTransport).

Asserts the seam the service exists for: every tool forwards to the RIGHT gateway path
with the caller's key as X-API-Key, and auth is fail-closed (401 with a Bearer hint).
"""
import json

from conftest import API_KEY, FakeGateway


# --- liveness ---------------------------------------------------------------

def test_health_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "mcp"}


# --- auth: fail-closed + the three accepted credential forms -----------------

def test_missing_credentials_401(client):
    r = client.get("/bot-status")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_bearer_token_forwarded_as_x_api_key(client, gateway, auth):
    r = client.get("/bot-status", headers=auth)
    assert r.status_code == 200
    assert gateway.requests[-1].headers["x-api-key"] == API_KEY


def test_raw_authorization_accepted(client, gateway):
    r = client.get("/bot-status", headers={"Authorization": API_KEY})
    assert r.status_code == 200
    assert gateway.requests[-1].headers["x-api-key"] == API_KEY


def test_x_api_key_accepted(client, gateway):
    r = client.get("/bot-status", headers={"X-API-Key": API_KEY})
    assert r.status_code == 200
    assert gateway.requests[-1].headers["x-api-key"] == API_KEY


# --- tools → gateway paths ---------------------------------------------------

def test_get_bot_status_path(client, gateway, auth):
    client.get("/bot-status", headers=auth)
    req = gateway.requests[-1]
    assert (req.method, req.url.path) == ("GET", "/bots/status")


def test_request_meeting_bot_with_native_id(client, gateway, auth):
    r = client.post(
        "/request-meeting-bot",
        headers=auth,
        json={"native_meeting_id": "abc-defg-hij", "platform": "google_meet", "bot_name": "Vexa"},
    )
    assert r.status_code == 200
    req = gateway.requests[-1]
    assert (req.method, req.url.path) == ("POST", "/bots")
    body = gateway.last_json()
    assert body["platform"] == "google_meet"
    assert body["native_meeting_id"] == "abc-defg-hij"
    assert body["bot_name"] == "Vexa"


def test_request_meeting_bot_with_url_parses_teams(client, gateway, auth):
    client.post(
        "/request-meeting-bot",
        headers=auth,
        json={"meeting_url": "https://teams.live.com/meet/9361792952021?p=IXw5Jh"},
    )
    body = gateway.last_json()
    assert body["platform"] == "teams"
    assert body["native_meeting_id"] == "9361792952021"
    assert body["passcode"] == "IXw5Jh"
    assert "meeting_url" not in body  # only legacy long Teams links forward the raw URL


def test_request_meeting_bot_url_and_id_rejected(client, auth):
    r = client.post(
        "/request-meeting-bot",
        headers=auth,
        json={"meeting_url": "https://meet.google.com/abc-defg-hij", "native_meeting_id": "abc-defg-hij"},
    )
    assert r.status_code == 422


def test_request_meeting_bot_409_reports_already_exists(client, gateway: FakeGateway, auth):
    gateway.routes[("POST", "/bots")] = (409, {"detail": "exists"})
    gateway.routes[("GET", "/meetings")] = (200, [
        {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "id": 7},
    ])
    r = client.post(
        "/request-meeting-bot",
        headers=auth,
        json={"native_meeting_id": "abc-defg-hij", "platform": "google_meet"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "already_exists"
    assert body["meeting"]["id"] == 7


def test_update_bot_config_path_and_payload(client, gateway, auth):
    r = client.put("/bot-config/teams/9361792952021", headers=auth, json={"language": "es"})
    assert r.status_code == 200
    req = gateway.requests[-1]
    assert (req.method, req.url.path) == ("PUT", "/bots/teams/9361792952021/config")
    assert gateway.last_json() == {"language": "es"}


def test_stop_bot_path(client, gateway, auth):
    client.delete("/bot/google_meet/abc-defg-hij", headers=auth)
    req = gateway.requests[-1]
    assert (req.method, req.url.path) == ("DELETE", "/bots/google_meet/abc-defg-hij")


def test_list_meetings_params(client, gateway, auth):
    client.get("/meetings?limit=5&offset=10&status=completed&platform=zoom", headers=auth)
    req = gateway.requests[-1]
    assert req.url.path == "/meetings"
    assert dict(req.url.params) == {"limit": "5", "offset": "10", "status": "completed", "platform": "zoom"}


def test_get_meeting_transcript_path(client, gateway, auth):
    client.get("/meeting-transcript/zoom/12345678901", headers=auth)
    req = gateway.requests[-1]
    assert (req.method, req.url.path) == ("GET", "/transcripts/zoom/12345678901")


def test_list_recordings_params(client, gateway, auth):
    client.get("/recordings?limit=3&offset=1&meeting_db_id=42", headers=auth)
    req = gateway.requests[-1]
    assert req.url.path == "/recordings"
    assert dict(req.url.params) == {"limit": "3", "offset": "1", "meeting_id": "42"}


def test_get_recording_path(client, gateway, auth):
    client.get("/recordings/42", headers=auth)
    req = gateway.requests[-1]
    assert (req.method, req.url.path) == ("GET", "/recordings/42")


def test_parse_meeting_link_no_gateway_hop(client, gateway, auth):
    r = client.post("/parse-meeting-link", headers=auth, json={"meeting_url": "https://zoom.us/j/12345678901?pwd=x"})
    assert r.status_code == 200
    assert r.json()["platform"] == "zoom"
    assert gateway.requests == []  # pure parse — never reaches the gateway


# --- error mapping: the gateway's status/detail is surfaced, not swallowed ----

def test_downstream_error_propagates(client, gateway: FakeGateway, auth):
    gateway.routes[("GET", "/bots/status")] = (403, {"detail": "Insufficient scope for this endpoint"})
    r = client.get("/bot-status", headers=auth)
    assert r.status_code == 403
    assert "Insufficient scope" in str(r.json()["detail"])


# --- report_issue: the mouth's write direction (biz#434, Grow-Mouth § The ticket) ------

SINK_URL = "http://sink.test/tickets"

TICKET = {
    "what_i_tried": "POST /bots for a google_meet room via the MCP request_meeting_bot tool",
    "what_happened": "The bot never appeared in the meeting and bot-status stayed empty for 3 min",
    "deployment": "self-hosted 0.12.3",
    "meeting_id": "abc-defg-hij",
    "platform": "google_meet",
    "logs": "RuntimeError: admission timeout",
}


def _sink_requests(gateway: FakeGateway):
    return [r for r in gateway.requests if r.url.host == "sink.test"]


def test_report_issue_without_sink_returns_503(client, gateway, auth, monkeypatch):
    monkeypatch.delenv("VEXA_TICKET_SINK_URL", raising=False)
    r = client.post("/report-issue", headers=auth, json=TICKET)
    assert r.status_code == 503
    assert "not configured" in str(r.json()["detail"])
    assert _sink_requests(gateway) == []  # nothing left the process


def test_report_issue_forwards_ticket_to_sink(client, gateway: FakeGateway, auth, monkeypatch):
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    r = client.post("/report-issue", headers=auth, json=TICKET)
    assert r.status_code == 200
    assert r.json()["status"] == "new"

    req = _sink_requests(gateway)[-1]
    assert (req.method, str(req.url)) == ("POST", SINK_URL)
    body = json.loads(req.content)
    assert body["what_i_tried"] == TICKET["what_i_tried"]
    assert body["what_happened"] == TICKET["what_happened"]
    assert body["deployment"] == "self-hosted 0.12.3"
    # the join key onto the cluster's own record of the same meeting
    assert body["meeting_id"] == "abc-defg-hij"
    assert body["platform"] == "google_meet"
    assert body["logs"] == "RuntimeError: admission timeout"
    assert body["logs_truncated"] is False
    assert body["source"] == "vexa-mcp"
    assert body["reported_at"]                      # server-side timestamp
    assert body["fingerprint"] == r.json()["fingerprint"]
    # canonical Linode-shaped pair, composed server-side so every ticket surface lands one shape
    assert body["summary"] == TICKET["what_happened"][:63] + "\u2026"
    assert len(body["summary"]) <= 64
    assert TICKET["what_i_tried"] in body["description"]
    assert TICKET["what_happened"] in body["description"]


def test_report_issue_response_mirrors_the_ticket_object(client, gateway: FakeGateway, auth, monkeypatch):
    """Linode's response shape: id · status · severity · opened · updated · opened_by · entity."""
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    gateway.routes[("POST", "/tickets")] = (200, {"id": 4711})
    r = client.post("/report-issue", headers=auth, json={**TICKET, "severity": 2, "version": "0.12.23"})
    body = r.json()
    assert body["id"] == 4711                       # the sink's id when the sink issues one
    assert body["status"] == "new"
    assert body["severity"] == 2
    assert body["opened"] == body["updated"]
    assert body["opened_by"] == json.loads(_sink_requests(gateway)[-1].content)["caller_fingerprint"]
    assert "entity" in body
    assert json.loads(_sink_requests(gateway)[-1].content)["version"] == "0.12.23"


def test_report_issue_id_falls_back_to_fingerprint(client, gateway, auth, monkeypatch):
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    r = client.post("/report-issue", headers=auth, json=TICKET).json()
    assert r["id"] == r["fingerprint"]              # no store here; the sink issues ids or we don't


def test_report_issue_severity_out_of_range_rejected(client, auth, monkeypatch):
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    assert client.post("/report-issue", headers=auth, json={**TICKET, "severity": 9}).status_code == 422


def test_report_issue_fingerprint_is_stable_and_content_derived(client, gateway, auth, monkeypatch):
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    first = client.post("/report-issue", headers=auth, json=TICKET).json()["fingerprint"]
    same = client.post("/report-issue", headers=auth, json={**TICKET, "logs": "other"}).json()["fingerprint"]
    other = client.post(
        "/report-issue", headers=auth, json={**TICKET, "what_happened": "totally different failure"}
    ).json()["fingerprint"]
    assert first == same        # dedupe key: deployment + what_happened
    assert first != other


def test_report_issue_never_forwards_the_api_key(client, gateway: FakeGateway, auth, monkeypatch):
    """The sink gets a salted fingerprint of the key, never the credential itself."""
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    monkeypatch.setenv("VEXA_TICKET_SINK_TOKEN", "sink-secret")
    client.post("/report-issue", headers=auth, json=TICKET)

    req = _sink_requests(gateway)[-1]
    raw = req.content.decode()
    assert API_KEY not in raw
    assert API_KEY not in json.dumps(dict(req.headers))
    assert "x-api-key" not in {k.lower() for k in req.headers}
    body = json.loads(raw)
    assert len(body["caller_fingerprint"]) == 16
    assert body["caller_fingerprint"] != API_KEY
    # the sink's own token authenticates this hop, not the caller's key
    assert req.headers["authorization"] == "Bearer sink-secret"


def test_report_issue_caller_fingerprint_is_stable_per_key(client, gateway: FakeGateway, auth, monkeypatch):
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    client.post("/report-issue", headers=auth, json=TICKET)
    mine = json.loads(_sink_requests(gateway)[-1].content)["caller_fingerprint"]
    client.post("/report-issue", headers=auth, json=TICKET)
    again = json.loads(_sink_requests(gateway)[-1].content)["caller_fingerprint"]
    client.post("/report-issue", headers={"X-API-Key": "someone-else"}, json=TICKET)
    theirs = json.loads(_sink_requests(gateway)[-1].content)["caller_fingerprint"]
    assert mine == again
    assert mine != theirs


def test_report_issue_truncates_oversized_logs(client, gateway: FakeGateway, auth, monkeypatch):
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    r = client.post("/report-issue", headers=auth, json={**TICKET, "logs": "x" * 9000})
    assert r.status_code == 200
    body = json.loads(_sink_requests(gateway)[-1].content)
    assert len(body["logs"]) == 4000
    assert body["logs_truncated"] is True


def test_report_issue_caps_long_text_fields(client, gateway: FakeGateway, auth, monkeypatch):
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    client.post("/report-issue", headers=auth, json={**TICKET, "what_happened": "y" * 9000})
    body = json.loads(_sink_requests(gateway)[-1].content)
    assert len(body["what_happened"]) == 2000


def test_report_issue_rejects_empty_fields(client, auth, monkeypatch):
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    r = client.post("/report-issue", headers=auth, json={**TICKET, "what_happened": "   "})
    assert r.status_code == 422
    r = client.post("/report-issue", headers=auth, json={"what_i_tried": "a"})
    assert r.status_code == 422


def test_report_issue_requires_auth(client, auth, monkeypatch):
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    r = client.post("/report-issue", json=TICKET)
    assert r.status_code == 401


def test_report_issue_sink_failure_surfaces_as_502(client, gateway: FakeGateway, auth, monkeypatch):
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    gateway.routes[("POST", "/tickets")] = (500, {"detail": "boom"})
    r = client.post("/report-issue", headers=auth, json=TICKET)
    assert r.status_code == 502
    assert "sink rejected" in str(r.json()["detail"])


def test_report_issue_without_meeting_id_never_touches_the_gateway(client, gateway: FakeGateway, auth, monkeypatch):
    """Stateless by design: with no entity to resolve, a ticket goes to the sink only."""
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    payload = {k: v for k, v in TICKET.items() if k not in ("meeting_id", "platform")}
    client.post("/report-issue", headers=auth, json=payload)
    assert [r for r in gateway.requests if r.url.host == "gateway.test"] == []
    assert json.loads(_sink_requests(gateway)[-1].content)["entity"] is None


def test_report_issue_resolves_entity_only_for_a_meeting_the_caller_owns(
    client, gateway: FakeGateway, auth, monkeypatch
):
    """The entity pointer is authorisation-checked: resolution runs through the GATEWAY with the
    caller's own key, so an id the key does not own never resolves."""
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    gateway.routes[("GET", "/meetings")] = (200, [
        {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "id": 7},
    ])
    r = client.post("/report-issue", headers=auth, json=TICKET)
    assert r.json()["entity"] == {
        "type": "meeting",
        "id": "abc-defg-hij",
        "platform": "google_meet",
        "url": "/transcripts/google_meet/abc-defg-hij",
    }
    # the ownership check is the gateway's, made with the caller's key — never a local decision
    lookup = [q for q in gateway.requests if q.url.path == "/meetings"][-1]
    assert lookup.headers["x-api-key"] == API_KEY
    assert json.loads(_sink_requests(gateway)[-1].content)["entity"]["id"] == "abc-defg-hij"


def test_report_issue_unowned_meeting_id_files_without_an_entity(client, gateway: FakeGateway, auth, monkeypatch):
    """A quoted id the caller does not own is text, never a resolved join — and never fatal."""
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    gateway.routes[("GET", "/meetings")] = (403, {"detail": "not yours"})
    r = client.post("/report-issue", headers=auth, json={**TICKET, "meeting_id": "someone-elses"})
    assert r.status_code == 200
    assert r.json()["entity"] is None
    body = json.loads(_sink_requests(gateway)[-1].content)
    assert body["entity"] is None
    assert body["meeting_id"] == "someone-elses"     # quoted, not resolved


def test_report_issue_meeting_absent_from_callers_meetings_does_not_resolve(
    client, gateway: FakeGateway, auth, monkeypatch
):
    """The gateway answers 200 with the caller's OWN meetings; an id that is not among them is not
    the caller's, so it must not resolve — the entity comes from the answer, never from the input."""
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    gateway.routes[("GET", "/meetings")] = (200, [
        {"platform": "google_meet", "native_meeting_id": "some-other-room", "id": 1},
    ])
    r = client.post("/report-issue", headers=auth, json=TICKET)
    assert r.status_code == 200
    assert r.json()["entity"] is None
    assert json.loads(_sink_requests(gateway)[-1].content)["entity"] is None


# --- prod-owner acceptance conditions (design note § 4b) ---------------------

def test_report_issue_has_no_url_shaped_field_and_fetches_nothing(client, gateway: FakeGateway, auth, monkeypatch):
    """SSRF closed by construction: no field the server dereferences, and the only URL opened is
    the operator's env-configured sink."""
    from vexa_mcp.app import ReportIssue

    fields = set(ReportIssue.model_fields)
    assert not [f for f in fields if "url" in f.lower() or "uri" in f.lower() or "link" in f.lower()]

    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    poisoned = {
        **{k: v for k, v in TICKET.items() if k not in ("meeting_id", "platform")},
        "what_happened": "see http://169.254.169.254/latest/meta-data/ and file:///etc/passwd",
        "logs": "http://localhost:6379/ http://internal.svc/admin",
    }
    r = client.post("/report-issue", headers=auth, json=poisoned)
    assert r.status_code == 200
    assert {q.url.host for q in gateway.requests} == {"sink.test"}   # nothing in the text was fetched
    assert "169.254.169.254" in json.loads(_sink_requests(gateway)[-1].content)["what_happened"]


def test_report_issue_body_size_cap(client, gateway: FakeGateway, auth, monkeypatch):
    """A whole-body ceiling, refused before the JSON parser sees it."""
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    r = client.post("/report-issue", headers=auth, json={**TICKET, "logs": "x" * 200_000})
    assert r.status_code == 413
    assert _sink_requests(gateway) == []


def test_ticket_path_touches_no_database():
    """The sink is isolated from the meetings DB by construction: this service has no DB at all —
    no driver, no session, no ORM anywhere in the package it could reach account state through."""
    import ast
    import pathlib

    import vexa_mcp

    pkg = pathlib.Path(vexa_mcp.__file__).parent
    banned = {"sqlalchemy", "psycopg", "psycopg2", "asyncpg", "sqlite3", "redis", "aioredis",
              "databases", "alembic", "pymongo"}
    for path in sorted(pkg.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            hit = banned.intersection(names)
            assert not hit, f"{path.name} imports {hit} — the ticket path must not reach account state"
