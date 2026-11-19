"""A re-created connection is findable from the old id; a slow service fails inside its own budget
(Vexa-ai/vexa#1783, frictions 2026-10-13).

* A person deleted a custom connection and created it again; the agent's next call with the old id
  answered a bare 404 "Connection not found". A deleted connection now answers 410: naming the
  connection that replaced it (same person, same kind, same name or same service host, created
  after it), or saying it was deleted. Another person's id still answers 404 — nothing is revealed.
* The broker's call to a connected service has one deadline (``SERVICE_DEADLINE_S``) shorter than
  agent-api's budget for the broker, so the broker — the hop that waited — says the service was
  slow. A GET is tried once more when that fits in the deadline; a POST only when no connection
  opened (nothing was sent).
"""
import json
import socket
from unittest.mock import MagicMock, patch

import pytest

from credential_broker import faults, secret_service

ENDPOINT = "https://chat.service.test/api/v10/users/@me"
AGENT_BUDGET_S = 25.0  # control_plane.routers.connections.BROKER_BUDGET_S


def _custom(signed, connection, ready, label, endpoint=ENDPOINT):
    cid = connection("custom_secret", label)
    r = signed("agent", "POST", f"/api/connections/{cid}/prepare", {"setup": {"endpoint": endpoint}})
    assert r.status_code == 200, r.text
    ready(cid)
    return cid


def _delete(signed, cid):
    r = signed("human", "POST", f"/api/connections/{cid}/delete")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- a re-created connection
def test_a_recreated_connection_answers_the_old_id_with_410_and_the_new_id(signed, connection, ready):
    old = _custom(signed, connection, ready, "Chat bot")
    _delete(signed, old)
    new = _custom(signed, connection, ready, "Chat bot")
    assert new != old
    listed = {c["id"]: c for c in signed("agent", "GET", "/api/connections").json()["connections"]}
    assert listed[new]["status"] == "ready" and old not in listed
    r = signed("agent", "POST", f"/api/connections/{old}/call", {})
    assert r.status_code == 410
    assert new in r.json()["detail"] and "replaced" in r.json()["detail"]


def test_a_successor_under_another_name_is_found_by_its_service_host(signed, connection, ready):
    old = _custom(signed, connection, ready, "Chat (support bot)")
    _delete(signed, old)
    new = _custom(signed, connection, ready, "Chat bot", ENDPOINT.replace("users/@me", "users/@me/guilds"))
    r = signed("agent", "POST", f"/api/connections/{old}/call", {})
    assert r.status_code == 410 and new in r.json()["detail"]


def test_a_deleted_connection_without_a_successor_says_it_was_deleted(signed, connection, ready):
    old = _custom(signed, connection, ready, "Chat bot")
    _delete(signed, old)
    _custom(signed, connection, ready, "Unrelated", "https://other.service.test/v1")
    r = signed("agent", "POST", f"/api/connections/{old}/call", {})
    assert r.status_code == 410
    assert "deleted" in r.json()["detail"] and "replaced" not in r.json()["detail"]


def test_another_persons_deleted_connection_is_still_not_found(signed, connection, ready):
    old = _custom(signed, connection, ready, "Chat bot")
    _delete(signed, old)
    _custom(signed, connection, ready, "Chat bot")
    assert signed("agent", "POST", f"/api/connections/{old}/call", {}, actor="other").status_code == 404


def test_the_410_is_an_error_the_contract_admits(signed, connection, ready):
    import jsonschema
    from conftest import schema
    old = _custom(signed, connection, ready, "Chat bot")
    _delete(signed, old)
    body = signed("agent", "POST", f"/api/connections/{old}/call", {}).json()
    defs = schema()["$defs"]
    jsonschema.validate(body, {**defs["Error"], "$defs": defs})


# ---------------------------------------------------------------- the service deadline
def test_the_service_deadline_ends_well_inside_agent_apis_budget():
    assert secret_service.SERVICE_DEADLINE_S <= AGENT_BUDGET_S / 2


def _config(method="GET"):
    return secret_service.configure("private-token", "https://fixture.test/v1", "Authorization", "bearer", method)


def _answer(conn, status=200, body=b'{"ok":true}'):
    response = conn.getresponse.return_value
    response.status = status
    response.read.side_effect = None
    response.read.return_value = body
    return conn


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(secret_service, "_retry_sleep", lambda s: None)


def test_a_get_that_timed_out_is_tried_once_more(no_wait):
    slow, fast = MagicMock(), _answer(MagicMock())
    slow.getresponse.side_effect = socket.timeout("timed out")
    with patch.object(secret_service, "public_addresses", return_value=["8.8.8.8"]), \
            patch.object(secret_service, "PinnedHTTPS", side_effect=[slow, fast]):
        assert secret_service.execute(_config(), {})["content"] == {"ok": True}


def test_a_post_that_may_have_been_sent_is_never_repeated(no_wait):
    slow = MagicMock()
    slow.getresponse.side_effect = socket.timeout("timed out")
    with patch.object(secret_service, "public_addresses", return_value=["8.8.8.8"]), \
            patch.object(secret_service, "PinnedHTTPS", side_effect=[slow]) as made:
        with pytest.raises(faults.UpstreamFault) as exc:
            secret_service.execute(_config("POST"), {}, body={})
    assert made.call_count == 1
    assert "did not answer within" in str(exc.value)


def test_a_post_whose_connection_never_opened_is_sent_again(no_wait):
    refused, fast = MagicMock(), _answer(MagicMock())
    refused.connect.side_effect = ConnectionRefusedError("refused")
    with patch.object(secret_service, "public_addresses", return_value=["8.8.8.8"]), \
            patch.object(secret_service, "PinnedHTTPS", side_effect=[refused, fast]):
        assert secret_service.execute(_config("POST"), {}, body={})["http_status"] == 200


def test_retries_end_at_the_deadline(no_wait, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(secret_service, "_now", lambda: clock["t"])

    def slow_conn(*a):
        conn = MagicMock()

        def timeout(*_a, **_k):
            clock["t"] += secret_service.SERVICE_DEADLINE_S   # the first attempt uses it all
            raise socket.timeout("timed out")
        conn.getresponse.side_effect = timeout
        return conn
    with patch.object(secret_service, "public_addresses", return_value=["8.8.8.8"]), \
            patch.object(secret_service, "PinnedHTTPS", side_effect=slow_conn) as made:
        with pytest.raises(faults.UpstreamFault):
            secret_service.execute(_config(), {})
    assert made.call_count == 1


def test_a_slow_service_is_reported_by_the_broker_as_the_service(signed, connection, store, capsys):
    cid = connection("custom_secret", "Fixture")
    signed("human", "POST", f"/api/connections/{cid}/custom-secret", {"value": "SECRET", "endpoint": "https://fixture.test/v1"})
    with patch.object(secret_service, "execute",
                      side_effect=faults.UpstreamFault("service", "unreachable",
                                                       secret_service.LATE)):
        r = signed("agent", "POST", f"/api/connections/{cid}/call", {})
    assert r.status_code == 503
    assert r.json()["reason"] == "provider_error"
    assert "did not answer within" in r.json()["detail"]
    assert "SECRET" not in r.text and "SECRET" not in json.dumps(capsys.readouterr().out)
