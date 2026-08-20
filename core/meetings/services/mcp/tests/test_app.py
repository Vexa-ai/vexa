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
    assert r.json()["status"] == "received"

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


def test_report_issue_never_touches_the_gateway(client, gateway: FakeGateway, auth, monkeypatch):
    """Stateless by design: a ticket goes to the sink only — never past the gateway."""
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", SINK_URL)
    client.post("/report-issue", headers=auth, json=TICKET)
    assert [r for r in gateway.requests if r.url.host == "gateway.test"] == []
