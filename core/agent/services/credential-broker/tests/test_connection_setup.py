"""Prepared setups: a caller (often the agent) proposes, only a human save activates — and the first
save to a host the human has not approved must name that host back (M3)."""
import json
from unittest.mock import MagicMock, patch

import pytest

from credential_broker import connection_setup, secret_service
from conftest import OWNER

SPEC = {"endpoint": "https://api.telegram.org/bot{secret}/sendMessage", "scheme": "telegram", "method": "POST",
        "secret_label": "Bot token", "fields": [{"name": "chat_id", "label": "Chat ID", "location": "body"}]}


def prepare(signed, cid, spec=SPEC):
    r = signed("agent", "POST", f"/api/connections/{cid}/prepare", {"setup": spec})
    assert r.status_code == 200, r.text
    return next(x for x in signed("agent", "GET", "/api/connections").json()["connections"] if x["id"] == cid)


def test_proposal_requires_human_save_exact_revision_and_host(signed, connection, store):
    cid = connection("custom_secret", "Telegram")
    row = prepare(signed, cid)
    path = f"/api/connections/{cid}/custom-secret"
    payload = {"value": "123:fixture_token", "fields": {"chat_id": "-12345"}, "setup_request": row["setup_request"],
               "confirmed_host": "api.telegram.org"}
    assert signed("agent", "POST", path, payload).status_code == 403
    assert signed("human", "POST", path, {**payload, "setup_request": "old"}).status_code == 409
    assert signed("human", "POST", path, {**payload, "confirmed_host": ""}).json()["detail"] == "Confirm the destination host before saving"
    assert signed("human", "POST", path, {**payload, "confirmed_host": "api.telegram.org.evil.test"}).status_code == 409
    assert not store.puts(cid)
    assert signed("human", "POST", path, payload).status_code == 200
    assert store.rows[cid][-1]["value"]["fixed_body"] == {"chat_id": "-12345"}
    # The host is approved now: re-saving with the stored credential needs no second confirmation.
    assert signed("human", "POST", path, {**payload, "value": "", "confirmed_host": ""}).status_code == 200
    assert store.rows[cid][-1]["value"]["value"] == "123:fixture_token"
    assert "fixture_token" not in signed("agent", "GET", "/api/connections").text


def test_reprepared_setup_to_a_new_host_needs_confirmation_again(signed, connection):
    cid = connection("custom_secret", "Service")
    row = prepare(signed, cid, {"endpoint": "https://api.example.test/v1"})
    path = f"/api/connections/{cid}/custom-secret"
    assert signed("human", "POST", path, {"value": "k", "setup_request": row["setup_request"],
                                          "confirmed_host": "api.example.test"}).status_code == 200
    row = prepare(signed, cid, {"endpoint": "https://collector.attacker.test/v1"})
    body = {"value": "new-key", "setup_request": row["setup_request"]}
    assert signed("human", "POST", path, body).status_code == 409
    assert signed("human", "POST", path, {**body, "confirmed_host": "collector.attacker.test"}).status_code == 200


def test_human_entered_endpoint_needs_no_confirmation(signed, connection):
    cid = connection("custom_secret", "Mine")
    r = signed("human", "POST", f"/api/connections/{cid}/custom-secret",
               {"value": "k", "endpoint": "https://api.example.test/v1"})
    assert r.status_code == 200
    assert signed("human", "GET", "/api/connections").json()["connections"][0]["approved_host"] == "api.example.test"


def test_reuse_cannot_move_existing_secret_to_another_destination(signed, connection, ready, store):
    cid = connection("custom_secret", "Service")
    store.put(cid, {"owner": OWNER, "value": {"value": "fixture-secret", "endpoint": "https://api.example.test/one",
                              "header": "Authorization", "scheme": "bearer", "method": "GET"}})
    ready(cid)
    result = signed("human", "POST", f"/api/connections/{cid}/custom-secret", {"value": "", "endpoint": "https://other.example/two"})
    assert result.status_code == 409
    assert not store.puts(cid)[1:]
    assert "fixture-secret" not in result.text


def test_delete_owner_human_only_hides_and_disables(signed, connection):
    cid = connection("custom_secret", "Disposable fixture")
    path = f"/api/connections/{cid}"
    assert signed("agent", "POST", path + "/delete").status_code == 403
    assert signed("human", "POST", path + "/delete", actor="other").status_code == 404
    assert signed("human", "POST", path + "/delete").json() == {"connection_id": cid, "status": "deleted"}
    assert cid not in signed("agent", "GET", "/api/connections").text
    assert signed("agent", "POST", path + "/request").status_code == 404
    assert signed("human", "POST", path + "/custom-secret", {"value": "fixture"}).status_code == 404


def test_invalid_spec_and_foreign_owner_refused(signed, connection):
    cid = connection("custom_secret", "Fixture")
    path = f"/api/connections/{cid}/prepare"
    assert signed("agent", "POST", path, {"setup": SPEC}, actor="other").status_code == 404
    bad = {**SPEC, "endpoint": "https://other.test/bot{secret}/sendMessage"}
    assert signed("agent", "POST", path, {"setup": bad}).status_code == 422


def test_only_custom_secret_can_be_prepared(signed, connection):
    cid = connection("google_email", "Gmail")
    assert signed("agent", "POST", f"/api/connections/{cid}/prepare", {"setup": SPEC}).status_code == 409


def test_path_token_redaction_and_fixed_recipient():
    config = secret_service.configure("123:fixture_token", SPEC["endpoint"], "Authorization", "telegram", "POST")
    config["fixed_body"] = {"chat_id": "-12345"}
    c = MagicMock()
    c.getresponse.return_value.status = 200
    c.getresponse.return_value.read.return_value = b'{"echo":"123:fixture_token"}'
    with patch.object(secret_service, "public_addresses", return_value=["8.8.8.8"]), \
            patch.object(secret_service, "PinnedHTTPS", return_value=c):
        result = secret_service.execute(config, {}, {"text": "fixture"})
        assert c.request.call_args.args[:2] == ("POST", "/bot123:fixture_token/sendMessage")
        assert "Authorization" not in c.request.call_args.kwargs["headers"]
        assert json.loads(c.request.call_args.kwargs["body"])["chat_id"] == "-12345"
        assert "fixture_token" not in json.dumps(result)
        with pytest.raises(secret_service.ServiceError):
            secret_service.execute(config, {}, {"chat_id": "other"})


def test_generic_spec_and_no_get_body():
    assert connection_setup.validate({"endpoint": "https://api.example.com/v1"})["fields"] == []
    with pytest.raises(ValueError):
        connection_setup.validate({"endpoint": "https://api.example.com/v1", "fields": [{"name": "id", "label": "ID"}]})


@pytest.mark.parametrize("bad,named", [
    ({**SPEC, "service": "Telegram"}, "setup.service is not a setup field"),
    ({**SPEC, "fields": [{"name": "chat_id", "label": "Chat ID", "where": "body"}]},
     "setup.fields.0.where is not a setup field (allowed: name, label, location)"),
    ({"endpoint": "https://api.example.test/v1", "oauth": {"authorization_url": "https://a.test/o",
      "token_url": "https://a.test/t", "scopes": ["r"], "client_id": "x"}},
     "setup.oauth.client_id is not a setup field (allowed: authorization_url, token_url, scopes, token_auth)"),
    ({**SPEC, "method": "PUT"}, "setup.method:"),
    ({**SPEC, "endpoint": "https://other.test/bot{secret}/sendMessage"},
     "Choose a supported Telegram bot endpoint"),
])
def test_a_refused_proposal_names_the_field_and_never_echoes_a_value(signed, connection, bad, named):
    cid = connection("custom_secret", "Fixture")
    r = signed("agent", "POST", f"/api/connections/{cid}/prepare", {"setup": bad})
    assert r.status_code == 422
    assert named in r.json()["detail"], r.json()["detail"]
    assert "Telegram\"" not in r.json()["detail"] and "client_id\": \"x" not in r.json()["detail"]
