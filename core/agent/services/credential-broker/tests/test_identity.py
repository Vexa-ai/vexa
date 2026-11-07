"""The signed-role boundary: forged, expired, replayed and mis-bound assertions are refused, each
role is held to its routes, and connections are owned by the asserted actor."""
import json
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from credential_broker import assertion, providers


def test_forged_signature_refused(signed, client, keys, tmp_path):
    other = tmp_path / "attacker.key"
    other.write_text("attacker-chosen-key-" + "f" * 40)
    header = assertion.sign(other.read_bytes().strip(), role="human", actor="victim", session="s",
                            method="GET", path="/api/connections")
    r = client.get("/api/connections", headers={assertion.HEADER: header})
    assert r.status_code == 401


def test_agent_key_cannot_sign_human_role(signed):
    assert signed("human", "GET", "/api/connections", key_role="agent").status_code == 401
    assert signed("git", "POST", "/api/internal/git-secret", {"name": "pat/2", "action": "get"},
                  key_role="agent", actor="2").status_code == 401


@pytest.mark.parametrize("offset", [-31, -300, 6, 3600])
def test_expired_or_future_assertion_refused(signed, offset):
    assert signed("agent", "GET", "/api/connections", at=int(time.time()) + offset).status_code == 401


def test_fresh_assertion_inside_window_accepted(signed):
    assert signed("agent", "GET", "/api/connections", at=int(time.time()) - 20).status_code == 200


def test_tampering_and_replay_refused(signed, client):
    r = signed("human", "GET", "/api/connections")
    assert r.status_code == 200
    header = r.request.headers[assertion.HEADER]
    assert client.get("/api/connections", headers={assertion.HEADER: header}).status_code == 401
    assert client.post("/api/setup", headers={assertion.HEADER: header}, json={"provider": "google_email"}).status_code == 401


def test_body_and_path_binding(client, keys):
    key = Path(keys["agent"]).read_bytes().strip()
    body = json.dumps({"provider": "custom_secret", "label": "A"}).encode()
    header = assertion.sign(key, role="agent", actor="u", session="s", method="POST", path="/api/setup", body=body)
    other = json.dumps({"provider": "custom_secret", "label": "B"}).encode()
    assert client.post("/api/setup", content=other, headers={assertion.HEADER: header}).status_code == 401
    header = assertion.sign(key, role="agent", actor="u", session="s", method="GET", path="/api/connections")
    assert client.get("/api/connections?extra=1", headers={assertion.HEADER: header}).status_code == 401


def test_unconfigured_role_is_refused(broker, client, keys):
    key = Path(keys["git"]).read_bytes().strip()
    body = json.dumps({"name": "pat/2", "action": "get"}).encode()
    header = assertion.sign(key, role="git", actor="2", session="s", method="POST",
                            path="/api/internal/git-secret", body=body)
    broker.settings.key_files["git"] = ""      # a deployment that never switched the Git store over
    assert client.post("/api/internal/git-secret", content=body, headers={assertion.HEADER: header}).status_code == 401


def test_malformed_headers_refused(client):
    for header in ["", "x", "a.b.c", "!!!.deadbeef", "e30.00"]:
        assert client.get("/api/connections", headers={assertion.HEADER: header}).status_code == 401


def test_refusals_are_logged_by_kind_without_values(signed, capsys):
    signed("agent", "GET", "/api/connections", at=int(time.time()) - 999, actor="private-actor-id")
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    refused = [l for l in lines if l["event"] == "assertion_refused"]
    assert refused and refused[-1]["fields"]["kind"] == "expired"
    assert refused[-1]["fields"]["route"] == "/api/connections"
    assert "private-actor-id" not in json.dumps(lines)


def test_agent_requests_but_cannot_consent_or_read_other_users(signed, connection):
    cid = connection("google_email", "Gmail")
    assert signed("agent", "POST", f"/api/connections/{cid}/authorize").status_code == 403
    assert signed("human", "POST", f"/api/connections/{cid}/authorize", actor="other").status_code == 404
    assert signed("agent", "GET", "/api/connections", actor="other").json() == {"connections": []}


def test_harness_routes_are_not_served(signed):
    for method, path, body in [("POST", "/api/operator/google", {}), ("GET", "/api/state", None),
                               ("POST", "/api/connections/" + "a" * 32 + "/secret", {"value": "x"}),
                               ("POST", "/api/connections/" + "a" * 32 + "/execute", {"operation_id": "x" * 8})]:
        assert signed("human", method, path, body).status_code in (404, 405)


def test_pending_request_signal_is_owner_scoped(signed, connection):
    cid = connection("google_email", "Gmail")
    before = next(r["setup_request"] for r in signed("human", "GET", "/api/connections").json()["connections"] if r["id"] == cid)
    assert signed("agent", "POST", f"/api/connections/{cid}/request", actor="other").status_code == 404
    assert signed("agent", "POST", f"/api/connections/{cid}/request").status_code == 200
    rows = signed("human", "GET", "/api/connections").json()["connections"]
    assert len(rows) == 1 and rows[0]["setup_request"] != before


def test_callback_bound_to_session_and_single_use(signed, connection, store):
    cid = connection("google_calendar", "Calendar")
    r = signed("human", "POST", f"/api/connections/{cid}/authorize")
    q = parse_qs(urlsplit(r.json()["authorize_url"]).query)
    assert q["redirect_uri"] == ["https://app.example.test/api/auth/callback/google"]
    assert q["state"][0].startswith("vxc_")
    path = "/api/auth/callback/google?" + urlencode({"state": q["state"][0], "code": "fixture-code"})
    assert signed("human", "GET", path, session="another-session").status_code == 403
    with patch.object(providers, "tokens", return_value={"access_token": "DO-NOT-EXPOSE", "expires_at": 0, "scope": ""}):
        r = signed("human", "GET", path)
        assert r.json() == {"connection_id": cid, "status": "connected"}
    assert signed("human", "GET", path).status_code == 403
    assert "DO-NOT-EXPOSE" not in signed("agent", "GET", "/api/connections").text
    assert store.rows[cid][-1]["value"]["access_token"] == "DO-NOT-EXPOSE"


def test_git_role_is_separate_from_agent_and_human(signed):
    payload = {"name": "pat/2", "action": "get", "value": None}
    for role in ("agent", "human"):
        assert signed(role, "POST", "/api/internal/git-secret", payload, actor="2").status_code == 403
    assert signed("git", "GET", "/api/connections", actor="2").status_code == 403
    assert signed("git", "POST", "/api/internal/git-secret", payload, actor="other").status_code == 403
