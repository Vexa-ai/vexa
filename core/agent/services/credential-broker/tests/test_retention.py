"""What the broker keeps: deleting a connection destroys every stored version of its credential and
its OAuth application, disconnecting destroys the credential, and a write keeps only the current
version and the one before it."""
import json
import sqlite3

import httpx

from credential_broker.store import LocalEncryptedStore, OpenBaoStore

KEY = bytes(range(32))


def _saved(signed, connection, broker, store):
    cid = connection("custom_secret", "Service")
    assert signed("human", "POST", f"/api/connections/{cid}/custom-secret",
                  {"value": "fixture-secret", "endpoint": "https://api.service.test/v1"}).status_code == 200
    store.put("oauth-app-" + cid, {"value": {"client_secret": "S"}, "owner": "product-user"})
    return cid


def test_delete_destroys_every_stored_version(signed, connection, broker, store):
    cid = _saved(signed, connection, broker, store)
    assert signed("human", "POST", f"/api/connections/{cid}/delete").status_code == 200
    assert cid not in store.rows and "oauth-app-" + cid not in store.rows
    audit = broker.sql("SELECT outcome FROM audit WHERE action='connection.delete' ORDER BY seq", rows=True)
    assert [a["outcome"] for a in audit] == ["requested", "disabled_and_removed"]


def test_disconnect_destroys_the_credential_and_keeps_the_application(signed, connection, broker, store):
    cid = _saved(signed, connection, broker, store)
    assert signed("human", "POST", f"/api/connections/{cid}/disconnect").status_code == 200
    assert cid not in store.rows and "oauth-app-" + cid in store.rows


def test_a_store_that_cannot_delete_leaves_the_connection_as_it_was(signed, connection, broker, store):
    cid = _saved(signed, connection, broker, store)
    store.fail = "transport"
    assert signed("human", "POST", f"/api/connections/{cid}/delete").status_code == 503
    store.fail = None
    assert broker.sql("SELECT status FROM connections WHERE id=?", (cid,), one=True)["status"] == "ready"
    assert cid in store.rows


def test_the_local_store_keeps_two_versions_and_deletes_all(tmp_path):
    db = tmp_path / "secrets.sqlite"
    local = LocalEncryptedStore(db, KEY)
    for n in range(1, 5):
        local.put("abc", {"value": f"v{n}"})
    assert local.get("abc", version=2) is None and local.get("abc", version=1) is None
    assert local.get("abc", version=3).data == {"value": "v3"} and local.get("abc").data == {"value": "v4"}
    with sqlite3.connect(db) as c:
        ciphertexts = [r[0] for r in c.execute("SELECT ciphertext FROM secret_versions WHERE path='abc'")]
    local.delete("abc")
    assert local.get("abc") is None
    raw = db.read_bytes()
    assert all(ct not in raw for ct in ciphertexts)       # overwritten on disk, not left in free pages


def test_openbao_delete_destroys_the_key_and_its_versions(tmp_path):
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        return httpx.Response(204)
    token = tmp_path / "token"
    token.write_text("fixture-openbao-token\n")
    OpenBaoStore("https://bao:8200", token, transport=httpx.MockTransport(handler)).delete("abc")
    assert seen == [("DELETE", "/v1/connections/metadata/abc", None)]
