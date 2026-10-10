"""The agent role acts only for a person in the loop.

A worker dispatched without a person (a delegated identity whose regime is not ``human``) may list
connections, and nothing else: no setup, request, prepare, read, draft or call. agent-api refuses the
same verbs; the broker checks the gateway's signed identity itself, so the rule holds without it.
"""
import pytest

from conftest import GATEWAY_KEY

from credential_broker import identity_token


def _worker(regime, actor="product-user"):
    claims = {"sub": actor, "scopes": ["bot", "tx"], "delegation": {"regime": regime, "workspaces": "*"}}
    return identity_token.sign(GATEWAY_KEY, claims)


@pytest.fixture
def cid(connection):
    return connection("custom_secret", "Service")


UNWATCHED_REFUSED = [
    ("POST", "/api/setup", {"provider": "custom_secret", "label": "x"}),
    ("POST", "/api/connections/{cid}/request", None),
    ("POST", "/api/connections/{cid}/prepare", {"setup": {"endpoint": "https://api.service.test/v1"}}),
    ("POST", "/api/connections/{cid}/read", {"action": "gmail.search"}),
    ("POST", "/api/connections/{cid}/draft", {"request_id": "fixture-draft-1", "recipient": "a@b.test",
                                              "subject": "s", "body": "b"}),
    ("POST", "/api/connections/{cid}/call", {"parameters": {}}),
]


@pytest.mark.parametrize("regime", ["autonomous", "", "HUMANISH"])
@pytest.mark.parametrize("method,path,body", UNWATCHED_REFUSED)
def test_a_worker_without_a_person_is_refused(signed, cid, regime, method, path, body):
    r = signed("agent", method, path.format(cid=cid), body, identity=_worker(regime))
    assert r.status_code == 403
    assert r.json()["detail"] == "This session runs without a person in the loop"


def test_a_worker_without_a_person_may_list(signed, cid):
    r = signed("agent", "GET", "/api/connections", identity=_worker("autonomous"))
    assert r.status_code == 200 and r.json()["connections"][0]["id"] == cid


def test_a_worker_with_a_person_in_the_loop_is_served(signed, cid):
    r = signed("agent", "POST", "/api/setup", {"provider": "custom_secret", "label": "x"}, identity=_worker("human"))
    assert r.status_code == 200
    r = signed("agent", "POST", f"/api/connections/{cid}/request", None, identity=_worker("Human"))
    assert r.status_code == 200
