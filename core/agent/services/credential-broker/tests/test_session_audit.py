"""The broker audits a worker's credential use under the session the gateway signed (Vexa-ai/vexa#1783).

A worker's identity names the agent unit — the chat or job — its delegation was minted for
(gateway-identity.v1 ``delegation.unit``). agent-api asserts that unit as the session; the broker
refuses an assertion that names any other, records the unit as ``audit.session``, logs under it, and
continues the caller's ``X-Trace-Id``.
"""
from conftest import GATEWAY_KEY

from credential_broker import identity_token, obs

UNIT = "agent-product-user-chat-main"


def _worker(unit=UNIT):
    claims = {"sub": "product-user", "scopes": ["bot", "tx"],
              "delegation": {"regime": "human", "workspaces": "*", "unit": unit}}
    return identity_token.sign(GATEWAY_KEY, claims)


def test_a_workers_credential_use_is_audited_under_its_signed_session(signed, broker, connection, capsys):
    cid = connection("custom_secret", "Service")
    capsys.readouterr()   # the fixture's own setup line is a person's, not the worker's
    r = signed("agent", "POST", f"/api/connections/{cid}/request", None, session=UNIT,
               identity=_worker(), headers={"X-Trace-Id": "turn-trace-7"})
    assert r.status_code == 200, r.text
    assert r.headers["x-trace-id"] == "turn-trace-7"
    last = broker.sql("SELECT session FROM audit WHERE connection=? ORDER BY seq DESC LIMIT 1", (cid,), one=True)
    assert last == {"session": UNIT}
    lines = [l for l in capsys.readouterr().out.splitlines() if '"connection_audit"' in l]
    assert lines and all(f'"session":"{UNIT}"' in l and '"trace_id":"turn-trace-7"' in l for l in lines)


def test_an_assertion_naming_another_session_is_refused(signed, connection):
    cid = connection("custom_secret", "Service")
    r = signed("agent", "POST", f"/api/connections/{cid}/request", None, session="agent-someone-else",
               identity=_worker())
    assert r.status_code == 401


def test_a_persons_own_identity_keeps_the_asserted_session(signed, broker, connection):
    cid = connection("custom_secret", "Service")
    person = identity_token.sign(GATEWAY_KEY, {"sub": "product-user", "scopes": ["bot", "tx"]})
    r = signed("agent", "POST", f"/api/connections/{cid}/request", None, session="agent-request-1",
               identity=person)
    assert r.status_code == 200, r.text
    last = broker.sql("SELECT session FROM audit WHERE connection=? ORDER BY seq DESC LIMIT 1", (cid,), one=True)
    assert last == {"session": "agent-request-1"}
