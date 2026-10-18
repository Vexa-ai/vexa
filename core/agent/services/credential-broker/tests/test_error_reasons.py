"""Every refusal and fault carries a typed `reason` beside its sentence (credential-broker.v1 Error).

agent-api and the agent must tell an outage of this deployment from an authorization the person can
renew: `store_unavailable` (reconnecting cannot help), `reconnect_required` (it can), and
`provider_error` (it cannot). A reconnect sentence that is not listed in `reasons.RECONNECT` would
be answered `provider_error`, so every one this package raises is checked against the list.
"""
import re
import time
from pathlib import Path
from unittest.mock import patch

from conftest import OWNER

from credential_broker import providers, secret_service
from credential_broker.faults import UpstreamFault

SRC = Path(__file__).resolve().parents[1] / "src" / "credential_broker"


def test_every_reconnect_sentence_is_listed():
    from credential_broker import reasons

    raised = set()
    for f in SRC.glob("*.py"):
        for sentence in re.findall(r'"([^"\n]*reconnect[^"\n]*)"|\'([^\'\n]*reconnect[^\'\n]*)\'', f.read_text()):
            text = next(s for s in sentence if s)
            if text[0].isupper() and ";" in text:
                raised.add(text)
    assert raised and raised <= reasons.RECONNECT, raised - reasons.RECONNECT


def _gmail(signed, connection, ready, store):
    cid = connection("google_email")
    store.put(cid, {"owner": OWNER, "value": {"access_token": "t", "refresh_token": "", "expires_at": 0,
                                               "scope": providers.DRAFT_SCOPE}})
    ready(cid)
    return cid


def test_an_expired_authorization_is_reconnect_required(signed, connection, ready, store):
    cid = _gmail(signed, connection, ready, store)
    r = signed("agent", "POST", f"/api/connections/{cid}/read", {"action": "gmail.search"})
    assert r.status_code == 409 and r.json()["reason"] == "reconnect_required"


def test_a_store_that_does_not_answer_is_store_unavailable(signed, connection, ready, store):
    cid = _gmail(signed, connection, ready, store)
    store.fail = "config"
    r = signed("agent", "POST", f"/api/connections/{cid}/read", {"action": "gmail.search"})
    assert r.status_code == 503 and r.json()["reason"] == "store_unavailable"


def test_a_provider_outage_is_provider_error(signed, connection, ready, store):
    cid = connection("google_email")
    store.put(cid, {"owner": OWNER, "value": {"access_token": "t", "expires_at": time.time() + 3600,
                                               "scope": providers.READ_SCOPE}})
    ready(cid)
    with patch.object(providers, "read_account", side_effect=UpstreamFault("provider", "unreachable", "down")):
        r = signed("agent", "POST", f"/api/connections/{cid}/read", {"action": "gmail.search"})
    assert r.status_code == 503 and r.json()["reason"] == "provider_error"


def test_a_custom_service_refusal_is_typed(signed, connection, store):
    cid = connection("custom_secret", "Service")
    assert signed("human", "POST", f"/api/connections/{cid}/custom-secret",
                  {"value": "k", "endpoint": "https://api.service.test/v1"}).status_code == 200
    with patch.object(secret_service, "execute", side_effect=secret_service.ServiceError("Authorization expired; reconnect")):
        r = signed("agent", "POST", f"/api/connections/{cid}/call", {"parameters": {}})
    assert r.status_code == 409 and r.json()["reason"] == "reconnect_required"
    with patch.object(secret_service, "execute", side_effect=secret_service.ServiceError("Method not allowed")):
        r = signed("agent", "POST", f"/api/connections/{cid}/call", {"parameters": {}})
    assert r.status_code == 409 and r.json()["reason"] == "provider_error"
