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


def test_a_worker_is_served_a_read_only_when_the_forwarded_policy_allows_it(signed, connection, ready, store):
    import time
    from unittest.mock import patch
    from credential_broker import providers
    from conftest import OWNER
    gmail = connection("google_email")
    store.put(gmail, {"owner": OWNER, "value": {"access_token": "t", "expires_at": time.time() + 500, "scope": ""}})
    ready(gmail)
    with patch.object(providers, "read_account", return_value={"messages": [], "has_more": False, "next_page_token": ""}):
        refused = signed("agent", "POST", f"/api/connections/{gmail}/read", {"action": "gmail.search"},
                         identity=_worker("autonomous"))
        assert refused.status_code == 403
        allowed = signed("agent", "POST", f"/api/connections/{gmail}/read",
                         {"action": "gmail.search", "policy": {"unattended": True}}, identity=_worker("autonomous"))
        assert allowed.status_code == 200


def test_a_worker_reads_the_persons_setting_but_cannot_change_it(signed):
    assert signed("agent", "GET", "/api/preferences", identity=_worker("autonomous")).status_code == 200
    r = signed("agent", "POST", "/api/preferences", {"draft_attachments": False}, identity=_worker("autonomous"))
    assert r.status_code == 403
    assert signed("agent", "POST", "/api/preferences", {"draft_attachments": False}).json() == {"draft_attachments": False}
    assert signed("human", "GET", "/api/preferences").json() == {"draft_attachments": False}
    assert signed("agent", "GET", "/api/preferences", actor="other").json() == {}


def test_the_consent_callback_asks_for_the_grant_it_started_with(signed, connection, broker):
    from unittest.mock import patch
    from credential_broker import providers
    cid = connection("google_calendar")
    url = signed("human", "POST", f"/api/connections/{cid}/authorize", {"grant": "calendar_write"}).json()["authorize_url"]
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(url).query)["state"][0]
    value = {"access_token": "t", "refresh_token": "r", "expires_at": 9e12,
             "scope": "https://www.googleapis.com/auth/calendar.events.readonly " + providers.CALENDAR_WRITE_SCOPE}
    with patch.object(providers, "tokens", return_value=value) as tokens:
        r = signed("human", "GET", f"/api/auth/callback/google?state={state}&code=fixture")
    assert r.json()["status"] == "connected" and tokens.call_args.kwargs["grant"] == "calendar_write"
    row = next(c for c in signed("human", "GET", "/api/connections").json()["connections"] if c["id"] == cid)
    assert row["permissions"] == ["calendar_write"]


# ── ADR-0048 decision 4: a turn other people read reaches no connection of its person's ────────
def _shared_worker(actor="product-user"):
    claims = {"sub": actor, "scopes": ["bot", "tx"],
              "delegation": {"regime": "human", "workspaces": ["team"], "shared": True}}
    return identity_token.sign(GATEWAY_KEY, claims)


@pytest.mark.parametrize("method,path,body", UNWATCHED_REFUSED + [("GET", "/api/connections", None)])
def test_a_shared_turn_is_refused_every_connection_verb(signed, cid, method, path, body):
    r = signed("agent", method, path.format(cid=cid), body, identity=_shared_worker())
    assert r.status_code == 403
    assert r.json()["detail"] == identity_token.SHARED_REFUSAL


def test_gmail_and_calendar_reads_are_refused_on_a_shared_turn(signed, connection):
    for provider in ("google_email", "google_calendar"):
        c = connection(provider, provider)
        action = "gmail.search" if provider == "google_email" else "calendar.events"
        r = signed("agent", "POST", f"/api/connections/{c}/read", {"action": action},
                   identity=_shared_worker())
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "shared_conversation"


def test_a_private_turn_with_a_person_is_still_served(signed, cid):
    r = signed("agent", "GET", "/api/connections", identity=_worker("human"))
    assert r.status_code == 200


@pytest.mark.parametrize("route,extra", [("draft", {}), ("draft-update", {"draft_id": "r-1"})])
def test_a_worker_without_a_person_never_writes_a_draft_whatever_policy_it_forwards(
        signed, connection, ready, store, route, extra):
    import time
    from unittest.mock import patch
    from credential_broker import providers
    from conftest import OWNER
    gmail = connection("google_email")
    store.put(gmail, {"owner": OWNER, "value": {"access_token": "t", "expires_at": time.time() + 500,
                                                "scope": providers.DRAFT_SCOPE}})
    ready(gmail)
    body = {"request_id": "fixture-draft-9", "recipient": "a@b.test", "subject": "s", "body": "b",
            "policy": {"unattended": True}, **extra}
    with patch.object(providers, "create_gmail_draft") as create:
        r = signed("agent", "POST", f"/api/connections/{gmail}/{route}", body, identity=_worker("autonomous"))
    assert r.status_code == 403 and r.json()["detail"] == "This session runs without a person in the loop"
    assert not create.called


def test_a_watched_worker_cannot_change_the_persons_setting_either(signed):
    r = signed("agent", "POST", "/api/preferences", {"draft_attachments": False}, identity=_worker("human"))
    assert r.status_code == 403
    assert signed("agent", "GET", "/api/preferences", identity=_worker("human")).json() == {}
