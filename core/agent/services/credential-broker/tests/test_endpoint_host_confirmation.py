"""M3 on OAuth setups: every host a credential goes to is confirmed by the person, the service
endpoint included — it receives the person's access token on every call."""
from conftest import OWNER

SPEC = {"endpoint": "https://api.service.test/v1/data",
        "oauth": {"authorization_url": "https://login.provider.test/authorize",
                  "token_url": "https://login.provider.test/token",
                  "scopes": ["read"], "token_auth": "client_secret_post"}, "fields": []}


def _prepare(signed, cid, spec):
    r = signed("agent", "POST", f"/api/connections/{cid}/prepare", {"setup": spec})
    assert r.status_code == 200, r.text
    return next(x for x in signed("human", "GET", "/api/connections").json()["connections"] if x["id"] == cid)


def _save(signed, cid, row, **confirmation):
    body = {"client_id": "id", "client_secret": "PRIVATE", "setup_request": row["setup_request"], **confirmation}
    return signed("human", "POST", f"/api/connections/{cid}/oauth-application", body)


def test_an_oauth_save_needs_the_service_endpoint_host_confirmed_too(signed, connection, store):
    cid = connection("custom_secret", "Service")
    row = _prepare(signed, cid, SPEC)
    assert _save(signed, cid, row, confirmed_host="login.provider.test").status_code == 409
    assert _save(signed, cid, row, confirmed_host="login.provider.test",
                 confirmed_hosts=["login.provider.test"]).status_code == 409
    assert not store.puts("oauth-app-" + cid)
    r = _save(signed, cid, row, confirmed_host="login.provider.test", confirmed_hosts=["api.service.test"])
    assert r.status_code == 200, r.text
    listed = signed("human", "GET", "/api/connections").json()["connections"][0]
    assert listed["approved_host"].split() == ["login.provider.test", "api.service.test"]
    assert store.rows["oauth-app-" + cid][-1]["owner"] == OWNER


def test_a_new_service_endpoint_host_is_confirmed_again(signed, connection):
    cid = connection("custom_secret", "Service")
    row = _prepare(signed, cid, SPEC)
    assert _save(signed, cid, row, confirmed_host="login.provider.test",
                 confirmed_hosts=["api.service.test"]).status_code == 200
    moved = {**SPEC, "endpoint": "https://collector.elsewhere.test/v1/data"}
    row = _prepare(signed, cid, moved)
    # The token host is approved already; the new endpoint host is not.
    assert _save(signed, cid, row).status_code == 409
    assert _save(signed, cid, row, confirmed_hosts=["collector.elsewhere.test"]).status_code == 200


def test_one_host_for_both_needs_one_confirmation(signed, connection):
    cid = connection("custom_secret", "Service")
    same = {**SPEC, "endpoint": "https://login.provider.test/v1/data"}
    row = _prepare(signed, cid, same)
    assert _save(signed, cid, row, confirmed_host="login.provider.test").status_code == 200
