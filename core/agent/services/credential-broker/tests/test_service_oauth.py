"""Generic OAuth for custom services: provider-neutral definitions, pinned exchange, human-only
application save bound to the exact proposal and its confirmed token host."""
import json
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from credential_broker import connection_setup, secret_service, service_oauth

SPEC = {"endpoint": "https://api.example.com/v2/data",
        "oauth": {"authorization_url": "https://login.example.com/authorize", "token_url": "https://api.example.com/token",
                  "scopes": ["read"], "token_auth": "client_secret_post"}, "fields": []}


def test_schema_accepts_provider_neutral_oauth_and_rejects_unsafe_urls():
    assert connection_setup.validate(SPEC)["oauth"]["scopes"] == ["read"]
    for url in ["http://example.com/token", "https://example.com/token?secret=x", "https://user:pass@example.com/token"]:
        with pytest.raises((ValueError, secret_service.ServiceError)):
            connection_setup.validate({**SPEC, "oauth": {**SPEC["oauth"], "token_url": url}})


def test_exchange_pins_dns_rotates_tokens_and_exposes_no_response_extras():
    conn = MagicMock()
    response = conn.getresponse.return_value
    response.status = 200
    response.read.return_value = json.dumps({"access_token": "access", "refresh_token": "rotated", "expires_in": 3600,
                                             "token_type": "bearer", "secret": "private"}).encode()
    with patch.object(secret_service, "public_addresses", return_value=["93.184.216.34"]), \
            patch.object(secret_service, "PinnedHTTPS", return_value=conn) as transport:
        result = service_oauth.exchange(SPEC, {"client_id": "id", "client_secret": "private"}, refresh="old")
    transport.assert_called_once_with("api.example.com", "93.184.216.34")
    assert result["refresh_token"] == "rotated" and "private" not in json.dumps(result)
    assert parse_qs(conn.request.call_args.kwargs["body"].decode())["refresh_token"] == ["old"]


def test_private_resolution_is_rejected_before_credentials_sent():
    with patch.object(secret_service, "public_addresses", side_effect=secret_service.ServiceError("private")), \
            patch.object(secret_service, "PinnedHTTPS") as transport:
        with pytest.raises(secret_service.ServiceError):
            service_oauth.exchange(SPEC, {"client_id": "id", "client_secret": "private"}, refresh="old")
        transport.assert_not_called()


def prepared(signed, connection, broker):
    cid = connection("custom_secret", "Example")
    assert signed("agent", "POST", f"/api/connections/{cid}/prepare", {"setup": SPEC}).status_code == 200
    return cid, broker.sql("SELECT * FROM connections WHERE id=?", (cid,), one=True)


def test_application_save_is_human_owner_exact_and_host_confirmed(signed, connection, broker, store):
    cid, row = prepared(signed, connection, broker)
    body = {"client_id": "id", "client_secret": "PRIVATE", "setup_request": row["setup_request"], "confirmed_host": "api.example.com"}
    path = f"/api/connections/{cid}/oauth-application"
    assert signed("agent", "POST", path, body).status_code == 403
    assert signed("human", "POST", path, body, actor="other").status_code == 404
    assert signed("human", "POST", path, {**body, "setup_request": "stale"}).status_code == 409
    assert signed("human", "POST", path, {**body, "confirmed_host": "login.example.com"}).status_code == 409
    assert not store.puts("oauth-app-" + cid)
    r = signed("human", "POST", path, body)
    assert r.status_code == 200 and "PRIVATE" not in r.text
    assert "PRIVATE" not in json.dumps(broker.sql("SELECT * FROM connections", rows=True))
    assert "PRIVATE" not in json.dumps(broker.sql("SELECT * FROM audit", rows=True))
    listed = signed("agent", "GET", "/api/connections").json()["connections"][0]
    assert listed["application_configured"] and "PRIVATE" not in json.dumps(listed)


def test_generic_callback_binds_and_stores_approved_config(signed, connection, broker, store):
    cid, row = prepared(signed, connection, broker)
    signed("human", "POST", f"/api/connections/{cid}/oauth-application",
           {"client_id": "id", "client_secret": "PRIVATE", "setup_request": row["setup_request"], "confirmed_host": "api.example.com"})
    with patch.object(secret_service, "public_addresses", return_value=["93.184.216.34"]), \
            patch.object(service_oauth, "exchange", return_value={"access_token": "ACCESS", "refresh_token": "REFRESH", "expires_at": 99999999999}):
        r = signed("human", "POST", f"/api/connections/{cid}/authorize", {})
        state = parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]
        path = "/api/auth/callback/google?" + urlencode({"state": state, "code": "CODE"})
        r = signed("human", "GET", path)
        assert r.json() == {"connection_id": cid, "status": "connected"}
        assert "PRIVATE" not in r.text and "ACCESS" not in r.text
        assert signed("human", "GET", path).status_code == 403
    assert store.rows[cid][-1]["value"]["oauth_application"]["client_secret"] == "PRIVATE"


def test_stale_agent_cannot_downgrade_oauth_to_token_form(signed, connection, broker):
    cid, _ = prepared(signed, connection, broker)
    assert signed("agent", "POST", f"/api/connections/{cid}/prepare", {"setup": {"endpoint": "https://api.example.com/data"}}).status_code == 409
    assert json.loads(broker.sql("SELECT setup_spec FROM connections WHERE id=?", (cid,), one=True)["setup_spec"])["oauth"]


def test_identical_setup_does_not_invalidate_approved_application(signed, connection, broker):
    cid, _ = prepared(signed, connection, broker)
    broker.sql("UPDATE connections SET oauth_app_version=7 WHERE id=?", (cid,))
    assert signed("agent", "POST", f"/api/connections/{cid}/prepare", {"setup": SPEC}).status_code == 200
    assert broker.sql("SELECT oauth_app_version FROM connections WHERE id=?", (cid,), one=True)["oauth_app_version"] == 7
