"""A connection tool's failure says which kind it is, and what the agent does about it.

The broker's typed `reason` travels through agent-api's account tools to the agent, with one
instruction per reason (`connections.INSTRUCTIONS`): an outage of the deployment's credential store
is never answered as a reconnect, an expired authorization always points at `connection_request`,
and every tool's description carries the same failure rule.
"""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import route_policy
from control_plane.routers import connections

SIGNED = 'v1.signed-by-the-gateway.for-u1'
HUMAN = {'X-User-Id': 'u1', 'X-Vexa-Identity': SIGNED}
READY = {'connections': [{'id': 'a' * 32, 'provider': 'google_email', 'status': 'ready', 'label': 'Gmail'}]}


def _app():
    app = FastAPI(dependencies=[route_policy.PERSON_GATE])
    app.include_router(connections.build(subject_of=lambda r: r.headers['x-user-id']))
    return app


@pytest.fixture
def broker_answers(monkeypatch):
    """The real `call_broker` over a fake broker transport: the list answers ready, a read answers
    whatever the test sets."""
    state = {}

    def request(*, base_url, key_file, role, actor, method, path, payload=None, timeout=60, identity=''):
        if path == '/api/connections':
            return httpx.Response(200, json=READY)
        status, body = state['read']
        return httpx.Response(status, json=body)

    monkeypatch.setattr(connections.broker_client, 'request', request)
    return state


@pytest.mark.parametrize('status,body,reason,reconnect', [
    (503, {'detail': 'Credential store unavailable', 'reason': 'store_unavailable'}, 'store_unavailable', False),
    (409, {'detail': 'Authorization expired; reconnect this account', 'reason': 'reconnect_required'},
     'reconnect_required', True),
    (503, {'detail': 'Account read is unavailable; retry later', 'reason': 'provider_error'}, 'provider_error', False),
    (500, {'detail': 'x'}, 'broker_unreachable', False),
])
def test_a_gmail_tool_failure_names_its_reason_and_what_to_do(broker_answers, status, body, reason, reconnect):
    broker_answers['read'] = (status, body)
    r = TestClient(_app()).post('/api/connections/gmail/search', headers=HUMAN, json={'query': 'from:x'})
    detail = r.json()['detail']
    assert detail['reason'] == reason
    assert r.status_code == (503 if status == 500 else status)
    assert ('Call connection_request' in detail['instruction']) is reconnect
    if not reconnect:
        assert 'do not ask them to reconnect' in detail['instruction'].lower() or 'reconnecting does not help' in detail['instruction']


def test_a_store_outage_on_the_draft_tool_is_typed_too(broker_answers):
    broker_answers['read'] = (503, {'detail': 'Credential store unavailable', 'reason': 'store_unavailable'})
    r = TestClient(_app()).post('/api/connections/gmail/draft', headers=HUMAN,
                                json={'request_id': 'req-00000001', 'recipient': 'a@b.example',
                                      'subject': 's', 'body': 'b'})
    assert r.status_code == 503 and r.json()['detail']['reason'] == 'store_unavailable'


ACCOUNT_TOOLS = ['/api/connections/gmail/search', '/api/connections/gmail/inbox', '/api/connections/gmail/read',
                 '/api/connections/gmail/thread', '/api/connections/calendar/events',
                 '/api/connections/gmail/draft', '/api/connections/service/call']


def test_every_account_tool_carries_the_one_failure_rule():
    spec = _app().openapi()
    rule = ' '.join(connections.FAILURE_RULE.split())
    for path in ACCOUNT_TOOLS:
        assert rule in ' '.join(spec['paths'][path]['post']['description'].split()), path


def test_connection_request_is_the_verb_for_connect_and_for_reconnect_required():
    text = ' '.join(_app().openapi()['paths']['/api/connections/request']['post']['description'].split())
    assert 'asks to connect or reconnect an account' in text and 'reason=reconnect_required' in text
    assert 'Never answer such a request by retrying the tool that failed' in text


def test_the_panel_is_not_opened_while_the_credential_store_is_down(monkeypatch):
    """A consent is written to the store, so with the store down connection_request opens nothing
    and answers store_unavailable, whatever the agent decided."""
    sent = []
    monkeypatch.setattr(connections.broker_client, 'store_ready', lambda **kw: False)
    monkeypatch.setattr(connections, 'call_broker', lambda *a, **kw: sent.append(a) or {'connections': []})
    r = TestClient(_app()).post('/api/connections/request', headers=HUMAN, json={'provider': 'google_email'})
    assert r.status_code == 503 and r.json()['detail']['reason'] == 'store_unavailable'
    assert 'ui_action' not in r.text and sent == []


def test_the_panel_opens_while_the_store_answers(monkeypatch):
    monkeypatch.setattr(connections.broker_client, 'store_ready', lambda **kw: True)
    monkeypatch.setattr(connections, 'call_broker',
                        lambda actor, method, path, payload=None, *, identity:
                        {'connections': []} if path == '/api/connections' else {'connection_id': 'c' * 32, 'status': 'awaiting_user'})
    r = TestClient(_app()).post('/api/connections/request', headers=HUMAN, json={'provider': 'google_email'})
    assert r.status_code == 200 and r.json()['ui_action'] == 'open_connections'
