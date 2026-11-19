"""The agent tier is admitted per person at the edge (`VEXA_AGENT_ALLOW`).

Unset or empty admits everyone a scope admits (the behaviour before the setting existed); `*`
admits everyone; otherwise a comma-separated list of user ids and email addresses, compared without
case, where an entry starting with `@` admits every address at that domain. A caller the list does
not name is answered 403 `agent_tier_not_enabled` on every agent-domain row, before anything is
forwarded; every other domain is unchanged for them. A worker's delegation token is admitted by
the person it acts for, so the MCP's re-entry on an agent row is held to the same list.
"""
import pytest
from fastapi.testclient import TestClient

from gateway import create_app, identity_token
from conftest import SIGNING_KEY, FakeAuthorizer, FakeDownstream, FakeRedis, needs_agent

pytestmark = needs_agent

KEY = "vxa_test_unit_key"
USER_7 = {"user_id": 7, "scopes": ["bot", "tx"], "max_concurrent": 3, "email": "seven@example.com"}
USER_8 = {"user_id": 8, "scopes": ["bot"], "max_concurrent": 3, "email": "eight@other.test"}
REFUSED = {"detail": "agent_tier_not_enabled"}

AGENT_ROWS = [("POST", "/agent/chat"), ("GET", "/agent/workspace/tree"),
              ("GET", "/agent/sessions"), ("POST", "/agent/routines")]


def _client(user, allow, monkeypatch, *, identity_key=None, key=KEY):
    if allow is None:
        monkeypatch.delenv("VEXA_AGENT_ALLOW", raising=False)
    else:
        monkeypatch.setenv("VEXA_AGENT_ALLOW", allow)
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(user=user, valid_key=key), downstream, FakeRedis(),
                     agent_api_url="http://agent-api", identity_key=identity_key)
    return TestClient(app), downstream


def _call(client, method, path, key=KEY, extra=None):
    return client.request(method, path, headers={"x-api-key": key, **(extra or {})}, json={})


@pytest.mark.parametrize("method,path", AGENT_ROWS)
def test_a_caller_the_list_does_not_name_is_refused_before_any_forward(method, path, monkeypatch):
    client, downstream = _client(USER_8, "7", monkeypatch)
    r = _call(client, method, path)
    assert r.status_code == 403
    assert r.json() == REFUSED
    assert downstream.last is None


@pytest.mark.parametrize("method,path", AGENT_ROWS)
def test_a_caller_the_list_names_is_forwarded(method, path, monkeypatch):
    client, downstream = _client(USER_7, "7", monkeypatch)
    r = _call(client, method, path)
    assert r.status_code == 200
    assert downstream.last is not None
    assert downstream.last["url"].startswith("http://agent-api/api/")


def test_the_catch_all_is_held_to_the_same_list(monkeypatch):
    client, downstream = _client(USER_8, "7", monkeypatch)
    r = _call(client, "GET", "/agent/some/route/nobody/declared/by/name")
    assert r.status_code == 403 and r.json() == REFUSED
    assert downstream.last is None


def test_other_domains_are_unchanged_for_a_caller_the_list_does_not_name(monkeypatch):
    client, downstream = _client(USER_8, "7", monkeypatch)
    for method, path in [("GET", "/bots/status"), ("POST", "/bots")]:
        r = _call(client, method, path)
        assert r.status_code == 200, (method, path)
    assert downstream.last is not None and "agent-api" not in downstream.last["url"]


@pytest.mark.parametrize("allow", [None, "", "  ", "*"])
def test_unset_empty_or_star_admits_everyone(allow, monkeypatch):
    client, downstream = _client(USER_8, allow, monkeypatch)
    r = _call(client, "POST", "/agent/chat")
    assert r.status_code == 200
    assert downstream.last is not None


def test_a_domain_entry_admits_every_address_at_that_domain(monkeypatch):
    user = {**USER_8, "email": "Someone@Example.Test"}
    client, downstream = _client(user, "7, @example.test", monkeypatch)
    assert _call(client, "GET", "/agent/workspace/tree").status_code == 200
    client, downstream = _client({**USER_8, "email": "someone@notexample.test"},
                                 "@example.test", monkeypatch)
    assert _call(client, "GET", "/agent/workspace/tree").status_code == 403
    assert downstream.last is None


def test_an_email_entry_is_compared_without_case(monkeypatch):
    client, downstream = _client(USER_8, "EIGHT@Other.Test", monkeypatch)
    assert _call(client, "POST", "/agent/chat").status_code == 200


def _reentry(user):
    return identity_token.signed_headers(SIGNING_KEY, user)[identity_token.HEADER]


def test_a_worker_is_admitted_by_the_person_it_acts_for_on_the_mcp_re_entry(monkeypatch):
    dlg = {"regime": "autonomous", "workspaces": ["ws_1"], "target": "ws_1"}
    worker_for_8 = {**USER_8, "scopes": ["bot", "tx"], "delegation": dlg}
    token = "vxd_header.payload.signature"
    client, downstream = _client(worker_for_8, "7", monkeypatch, identity_key=SIGNING_KEY, key=token)
    r = _call(client, "GET", "/agent/workspace/tree", key=token,
              extra={"X-Vexa-Internal-Mcp-Identity": _reentry(worker_for_8)})
    assert r.status_code == 403 and r.json() == REFUSED
    assert downstream.last is None

    worker_for_7 = {**USER_7, "delegation": dlg}
    client, downstream = _client(worker_for_7, "7", monkeypatch, identity_key=SIGNING_KEY, key=token)
    r = _call(client, "GET", "/agent/workspace/tree", key=token,
              extra={"X-Vexa-Internal-Mcp-Identity": _reentry(worker_for_7)})
    assert r.status_code == 200
    assert downstream.last["url"] == "http://agent-api/api/workspace/tree"
