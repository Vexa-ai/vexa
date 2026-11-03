"""A stored credential carries its owner inside the encrypted value, and every read checks it.

The owner column in metadata.sqlite is plaintext on the state volume. Changing it must not hand a
connection's credential to anybody else: the sealed owner decides, and a mismatch is refused before
any credential is used. Records written before owners were sealed are sealed once, to the owner the
metadata names at that moment.
"""
import sqlite3
from unittest.mock import patch

from conftest import OWNER

from credential_broker import secret_service, service_oauth
from credential_broker.broker import Broker

OTHER = "someone-else"
CALLED = {"http_status": 200, "content": {"ok": True}, "untrusted_content": True}


def _saved_service(signed, connection):
    cid = connection("custom_secret", "Service")
    r = signed("human", "POST", f"/api/connections/{cid}/custom-secret",
               {"value": "fixture-secret", "endpoint": "https://api.service.test/v1"})
    assert r.status_code == 200, r.text
    return cid


def test_every_record_written_for_a_connection_names_its_owner(signed, connection, store):
    cid = _saved_service(signed, connection)
    assert store.rows[cid][-1]["owner"] == OWNER


def test_a_connection_reassigned_in_the_metadata_does_not_hand_over_its_credential(signed, connection, broker):
    cid = _saved_service(signed, connection)
    broker.sql("UPDATE connections SET actor=? WHERE id=?", (OTHER, cid))
    with patch.object(secret_service, "execute", return_value=CALLED) as execute:
        r = signed("agent", "POST", f"/api/connections/{cid}/call", {"parameters": {}}, actor=OTHER)
    assert r.status_code == 503
    execute.assert_not_called()


def test_the_owner_still_uses_the_credential(signed, connection):
    cid = _saved_service(signed, connection)
    with patch.object(secret_service, "execute", return_value=CALLED) as execute:
        r = signed("agent", "POST", f"/api/connections/{cid}/call", {"parameters": {}})
    assert r.status_code == 200 and execute.call_count == 1


def test_a_reassigned_oauth_application_does_not_start_a_consent(signed, connection, broker):
    spec = {"endpoint": "https://api.service.test/v1",
            "oauth": {"authorization_url": "https://login.service.test/authorize",
                      "token_url": "https://api.service.test/token", "scopes": ["read"],
                      "token_auth": "client_secret_post"}, "fields": []}
    cid = connection("custom_secret", "Service")
    assert signed("agent", "POST", f"/api/connections/{cid}/prepare", {"setup": spec}).status_code == 200
    row = broker.sql("SELECT * FROM connections WHERE id=?", (cid,), one=True)
    assert signed("human", "POST", f"/api/connections/{cid}/oauth-application",
                  {"client_id": "id", "client_secret": "PRIVATE", "setup_request": row["setup_request"],
                   "confirmed_host": "api.service.test"}).status_code == 200
    broker.sql("UPDATE connections SET actor=? WHERE id=?", (OTHER, cid))
    with patch.object(service_oauth, "authorize", return_value="https://login.service.test/authorize?x=1") as authorize:
        r = signed("human", "POST", f"/api/connections/{cid}/authorize", {}, actor=OTHER)
    assert r.status_code == 503
    authorize.assert_not_called()


def _legacy_state(broker, store):
    """Metadata and records as a broker that predates sealed owners left them."""
    rows = [("live", OWNER, "ready", 1, 1), ("gone", OWNER, "deleted", 1, 1), ("off", OWNER, "disconnected", 1, 1)]
    for cid, actor, status, version, app in rows:
        broker.sql("INSERT INTO connections(id,actor,session,label,status,version,created,oauth_app_version)"
                   " VALUES (?,?,?,?,?,?,?,?)", (cid, actor, "s", "L", status, version, 0, app))
        store.put(cid, {"value": {"access_token": "T-" + cid}})
        store.put("oauth-app-" + cid, {"value": {"client_id": "c", "client_secret": "S-" + cid}})
    try:
        broker.sql("DELETE FROM broker_meta")
    except sqlite3.OperationalError:
        pass                                          # a broker from before has no such table


def test_records_from_before_are_sealed_once_to_the_metadata_owner(settings, store, broker):
    _legacy_state(broker, store)
    after = Broker(settings, store)                   # the upgraded broker starts on that state
    live = after.sql("SELECT version, oauth_app_version FROM connections WHERE id='live'", one=True)
    assert store.rows["live"][live["version"] - 1] == {"value": {"access_token": "T-live"}, "owner": OWNER}
    assert store.rows["oauth-app-live"][live["oauth_app_version"] - 1]["owner"] == OWNER
    # A deleted connection keeps nothing; a disconnected one keeps only its application.
    assert "gone" not in store.rows and "oauth-app-gone" not in store.rows
    assert "off" not in store.rows and "oauth-app-off" in store.rows
    # Once: a second start rewrites nothing.
    puts = len([c for c in store.calls if c[0] == "put"])
    Broker(settings, store)
    assert len([c for c in store.calls if c[0] == "put"]) == puts


def test_a_record_without_an_owner_is_refused_once_owners_are_sealed(signed, connection, ready, store):
    cid = connection("custom_secret", "Service")
    store.put(cid, {"value": {"value": "x", "endpoint": "https://api.service.test/v1", "header": "Authorization",
                              "scheme": "bearer", "method": "GET"}})
    ready(cid)
    with patch.object(secret_service, "execute", return_value=CALLED) as execute:
        r = signed("agent", "POST", f"/api/connections/{cid}/call", {"parameters": {}})
    assert r.status_code == 503
    execute.assert_not_called()
