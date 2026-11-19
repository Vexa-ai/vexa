"""Connection calls are reliable, and a failure names its hop (Vexa-ai/vexa#1783, frictions 2026-10-13).

Observed on a live deployment: a custom-secret call answered `upstream timeout`, the next one a
sign-in outage; a connection the person had re-created answered a bare 404; a stale unfinished Gmail
setup was in the way of the account that was connected. What holds here:

* agent-api's whole budget for the credential broker, over every call one tool makes, ends before
  the gateway's forward does, so agent-api — not the gateway — says what was slow;
* a request is repeated only when repeating is safe: a read (GET) after any transport failure or a
  broker outage, any method when no connection opened (nothing was sent); never a POST that may
  have reached the broker;
* every fault says which hop failed;
* a connection that was deleted and re-created answers 410 naming the connection that replaced it;
* the default account is the ready one; when none is ready the answer names the setup that waits
  for the person and says to open Connections.
"""
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from control_plane import broker_client, route_policy
from control_plane.routers import connections

SIGNED = 'v1.signed-by-the-gateway.for-u1'
HUMAN = {'X-User-Id': 'u1', 'X-Vexa-Identity': SIGNED}
GATEWAY_FORWARD_TIMEOUT_S = 30.0  # gateway.adapters.FORWARD_TIMEOUT_S


@pytest.fixture
def keys(tmp_path, monkeypatch):
    agent = tmp_path / 'agent.key'
    agent.write_text('fixture-agent-key-' + 'a' * 40 + '\n')
    monkeypatch.setenv('VEXA_CONNECTIONS_BROKER_URL', 'http://credential-broker:8100')
    monkeypatch.setenv('VEXA_CONNECTIONS_AGENT_KEY_FILE', str(agent))


@pytest.fixture
def broker(monkeypatch, keys):
    """Every broker_client request goes to ``state['handler']``; what was sent, and with which
    timeout, is recorded. Retry pauses take no time."""
    state = {'sent': [], 'timeouts': [], 'slept': []}
    real = httpx.Client

    def client(**kwargs):
        state['timeouts'].append(kwargs.get('timeout'))

        def handle(req):
            state['sent'].append(req)
            return state['handler'](req)
        return real(transport=httpx.MockTransport(handle), **{k: v for k, v in kwargs.items() if k != 'transport'})
    monkeypatch.setattr(broker_client.httpx, 'Client', client)
    monkeypatch.setattr(connections, '_retry_sleep', lambda s: state['slept'].append(s))
    return state


def _raise(exc):
    def handler(req):
        raise exc
    return handler


def _flaky(first, then):
    calls = {'n': 0}

    def handler(req):
        calls['n'] += 1
        return first(req) if calls['n'] == 1 else then(req)
    return handler


OK_LIST = lambda req: httpx.Response(200, json={'connections': []})  # noqa: E731


# ---------------------------------------------------------------- the budget and the hop order
def test_the_broker_budget_ends_before_the_gateway_forward():
    assert connections.BROKER_BUDGET_S <= GATEWAY_FORWARD_TIMEOUT_S - 5


def test_one_call_never_waits_longer_than_the_budget(broker):
    broker['handler'] = OK_LIST
    connections.call_broker('u1', 'GET', '/api/connections', identity=SIGNED)
    assert broker['timeouts'] and all(t <= connections.BROKER_BUDGET_S for t in broker['timeouts'])


def test_every_call_one_tool_makes_shares_one_budget(broker, monkeypatch):
    """A Gmail read makes three broker calls (the person's setting, the list, the read). Each one
    is given what is LEFT of the tool's budget, so together they end before the gateway's forward."""
    clock = {'t': 1000.0}
    monkeypatch.setattr(connections, '_now', lambda: clock['t'])
    ready = {'connections': [{'id': 'a' * 32, 'provider': 'google_email', 'status': 'ready', 'label': 'Gmail'}]}

    def handler(req):
        clock['t'] += 6.0   # every broker answer takes six seconds
        if req.url.path == '/api/preferences':
            return httpx.Response(200, json={})
        if req.url.path == '/api/connections':
            return httpx.Response(200, json=ready)
        return httpx.Response(200, json={'messages': []})
    broker['handler'] = handler
    app = FastAPI(dependencies=[route_policy.PERSON_GATE])
    app.include_router(connections.build(subject_of=lambda r: r.headers['x-user-id']))
    r = TestClient(app).post('/api/connections/gmail/search', headers=HUMAN, json={'query': 'from:x'})
    assert r.status_code == 200
    assert broker['timeouts'][0] == pytest.approx(connections.BROKER_BUDGET_S)
    assert broker['timeouts'] == sorted(broker['timeouts'], reverse=True)
    assert broker['timeouts'][-1] == pytest.approx(connections.BROKER_BUDGET_S - 12.0)


# ---------------------------------------------------------------- bounded retry, idempotent only
def test_a_read_that_timed_out_is_asked_again_and_answers(broker):
    broker['handler'] = _flaky(_raise(httpx.ReadTimeout('slow')), OK_LIST)
    assert connections.call_broker('u1', 'GET', '/api/connections', identity=SIGNED) == {'connections': []}
    assert len(broker['sent']) == 2 and broker['slept']


def test_a_read_during_a_broker_outage_is_asked_again(broker):
    broker['handler'] = _flaky(lambda req: httpx.Response(503, json={'detail': 'Credential store unavailable',
                                                                    'reason': 'store_unavailable'}), OK_LIST)
    assert connections.call_broker('u1', 'GET', '/api/connections', identity=SIGNED) == {'connections': []}
    assert len(broker['sent']) == 2


def test_retries_are_bounded(broker):
    broker['handler'] = _raise(httpx.ReadTimeout('slow'))
    with pytest.raises(HTTPException):
        connections.call_broker('u1', 'GET', '/api/connections', identity=SIGNED)
    assert 1 < len(broker['sent']) <= 3


def test_a_post_that_may_have_reached_the_broker_is_never_repeated(broker):
    broker['handler'] = _raise(httpx.ReadTimeout('slow'))
    with pytest.raises(HTTPException) as exc:
        connections.call_broker('u1', 'POST', '/api/connections/' + 'c' * 32 + '/call', {'parameters': {}},
                                identity=SIGNED)
    assert len(broker['sent']) == 1
    assert exc.value.status_code == 504
    assert exc.value.detail['reason'] == 'broker_unreachable'
    assert exc.value.detail['hop'] == 'agent-api → credential-broker'
    assert 'did not answer' in exc.value.detail['message']


def test_a_post_whose_connection_never_opened_is_sent_again(broker):
    broker['handler'] = _flaky(_raise(httpx.ConnectError('refused')),
                               lambda req: httpx.Response(200, json={'operation_id': 'x'}))
    assert connections.call_broker('u1', 'POST', '/api/connections/' + 'c' * 32 + '/call', {'parameters': {}},
                                   identity=SIGNED) == {'operation_id': 'x'}
    assert len(broker['sent']) == 2


def test_a_post_answered_with_an_outage_is_not_repeated(broker):
    broker['handler'] = lambda req: httpx.Response(503, json={'detail': 'Service could not be reached',
                                                              'reason': 'provider_error'})
    with pytest.raises(HTTPException):
        connections.call_broker('u1', 'POST', '/api/connections/' + 'c' * 32 + '/call', {'parameters': {}},
                                identity=SIGNED)
    assert len(broker['sent']) == 1


# ---------------------------------------------------------------- every fault names its hop
@pytest.mark.parametrize('status,body,hop', [
    (503, {'detail': 'Credential store unavailable', 'reason': 'store_unavailable'},
     'credential-broker → credential store'),
    (503, {'detail': 'Service could not be reached; do not automatically retry a POST', 'reason': 'provider_error'},
     'credential-broker → connected service'),
    (502, {'detail': 'Account read is unavailable; the provider answered with an unreadable response'},
     'credential-broker → connected service'),
    (500, {'detail': 'x'}, 'agent-api → credential-broker'),
])
def test_a_fault_names_the_hop_that_failed(broker, status, body, hop):
    broker['handler'] = lambda req: httpx.Response(status, json=body)
    with pytest.raises(HTTPException) as exc:
        connections.call_broker('u1', 'POST', '/api/connections/' + 'c' * 32 + '/call', {'parameters': {}},
                                identity=SIGNED)
    assert exc.value.detail['hop'] == hop


# ---------------------------------------------------------------- a re-created connection
def test_a_replaced_connection_names_the_connection_that_replaced_it(broker):
    new = 'b' * 32
    broker['handler'] = lambda req: httpx.Response(410, json={
        'detail': f'This connection was deleted and replaced by connection {new}; use that connection_id'})
    with pytest.raises(HTTPException) as exc:
        connections.call_broker('u1', 'POST', '/api/connections/' + 'a' * 32 + '/call', {'parameters': {}},
                                identity=SIGNED)
    assert exc.value.status_code == 410
    assert exc.value.detail['reason'] == 'connection_replaced'
    assert exc.value.detail['replaced_by'] == new
    assert new in exc.value.detail['instruction'] or 'replaced_by' in exc.value.detail['instruction']


def test_a_deleted_connection_without_a_successor_says_so(broker):
    broker['handler'] = lambda req: httpx.Response(410, json={
        'detail': 'This connection was deleted; call connections_status for the current ones'})
    with pytest.raises(HTTPException) as exc:
        connections.call_broker('u1', 'POST', '/api/connections/' + 'a' * 32 + '/call', {'parameters': {}},
                                identity=SIGNED)
    assert exc.value.status_code == 410
    assert exc.value.detail['reason'] == 'connection_deleted'
    assert 'replaced_by' not in exc.value.detail


# ---------------------------------------------------------------- the default account
def _gmail_app(rows, monkeypatch):
    sent = []

    def call(actor, method, path, payload=None, *, identity):
        sent.append((method, path))
        if path == '/api/preferences':
            return {}
        if path == '/api/connections':
            return {'connections': rows}
        return {'messages': []}
    monkeypatch.setattr(connections, 'call_broker', call)
    app = FastAPI(dependencies=[route_policy.PERSON_GATE])
    app.include_router(connections.build(subject_of=lambda r: r.headers['x-user-id']))
    return TestClient(app), sent


def test_a_stale_unfinished_setup_does_not_stand_in_the_way_of_the_ready_account(monkeypatch):
    rows = [{'id': 'b' * 32, 'provider': 'google_email', 'status': 'awaiting_user', 'label': 'Gmail'},
            {'id': 'a' * 32, 'provider': 'google_email', 'status': 'ready', 'label': 'Gmail',
             'account': 'me@example.com'}]
    client, sent = _gmail_app(rows, monkeypatch)
    r = client.post('/api/connections/gmail/search', headers=HUMAN, json={'query': 'from:x'})
    assert r.status_code == 200
    assert sent[-1] == ('POST', '/api/connections/' + 'a' * 32 + '/read')


@pytest.mark.parametrize('status', ['awaiting_user', 'disconnected', 'error'])
def test_with_no_ready_account_the_answer_names_the_one_waiting_for_the_person(monkeypatch, status):
    rows = [{'id': 'b' * 32, 'provider': 'google_email', 'status': status, 'label': 'Work mail'}]
    client, _ = _gmail_app(rows, monkeypatch)
    r = client.post('/api/connections/gmail/search', headers=HUMAN, json={'query': 'from:x'})
    assert r.status_code == 409
    detail = r.json()['detail']
    assert detail['reason'] == 'not_ready'
    assert 'Work mail' in detail['message'] and 'b' * 32 in detail['message']
    assert 'Connections' in detail['message']
    assert 'connection_request' in detail['instruction']


def test_with_no_account_at_all_the_answer_says_to_connect_one(monkeypatch):
    client, _ = _gmail_app([], monkeypatch)
    r = client.post('/api/connections/gmail/search', headers=HUMAN, json={'query': 'from:x'})
    assert r.status_code == 409
    assert r.json()['detail']['reason'] == 'not_connected'


def test_several_ready_accounts_still_need_a_choice(monkeypatch):
    rows = [{'id': c * 32, 'provider': 'google_email', 'status': 'ready', 'label': c} for c in 'ab']
    rows.append({'id': 'c' * 32, 'provider': 'google_email', 'status': 'awaiting_user', 'label': 'c'})
    client, _ = _gmail_app(rows, monkeypatch)
    r = client.post('/api/connections/gmail/search', headers=HUMAN, json={'query': 'from:x'})
    assert r.status_code == 409
    assert 'connections_status' in str(r.json()['detail'])
