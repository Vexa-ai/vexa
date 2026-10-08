"""The Connections routes the assembled MCP serves as tools (core/agent/mcp.tools.v1.json).

Each tool is one typed route: the broker is faked at `call_broker`, so these pin what each route
asks the broker for, what it returns to the agent (metadata, never a credential), and that every
verb needing a person in the loop refuses a worker dispatched without one.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.routers import connections

READY = [{'id': 'a' * 32, 'provider': 'google_email', 'status': 'ready', 'label': 'Gmail',
          'access_token': 'never-shown'},
         {'id': 'b' * 32, 'provider': 'google_calendar', 'status': 'ready', 'label': ''}]


@pytest.fixture
def broker(monkeypatch):
    calls = []

    def fake(actor, method, path, payload=None):
        calls.append((actor, method, path, payload))
        if path == '/api/connections':
            return {'connections': READY}
        return {'ok': True, 'path': path}

    monkeypatch.setattr(connections, 'call_broker', fake)
    return calls


def _person(request):
    regime = (request.headers.get('x-user-regime') or '').lower()
    if regime and regime != 'human':
        from fastapi import HTTPException
        raise HTTPException(403, {'status': 'refused', 'reason': 'human_session_required'})


@pytest.fixture
def client(broker):
    app = FastAPI()
    app.include_router(connections.build(subject_of=lambda r: r.headers['x-user-id'],
                                         require_person=_person))
    return TestClient(app)


HUMAN = {'X-User-Id': 'u1'}
WORKER = {'X-User-Id': 'u1', 'X-User-Regime': 'autonomous'}


def test_status_returns_metadata_only(client):
    body = client.get('/api/connections', headers=WORKER).json()
    assert body['connections'][0] == {'id': 'a' * 32, 'provider': 'google_email', 'status': 'ready',
                                      'label': 'Gmail'}
    assert 'access_token' not in str(body)


@pytest.mark.parametrize('path,payload,action', [
    ('/api/connections/gmail/search', {'query': 'from:x', 'limit': 2}, 'gmail.search'),
    ('/api/connections/gmail/inbox', {'limit': 3}, 'gmail.search'),
    ('/api/connections/gmail/read', {'message_id': 'm1'}, 'gmail.read'),
    ('/api/connections/gmail/thread', {'thread_id': 't1'}, 'gmail.thread'),
    ('/api/connections/calendar/events', {'time_min': '2026-10-01T00:00:00Z',
                                          'time_max': '2026-10-08T00:00:00Z'}, 'calendar.events'),
])
def test_each_read_tool_is_one_typed_route_onto_the_fixed_broker_read(client, broker, path, payload, action):
    r = client.post(path, headers=HUMAN, json=payload)
    assert r.status_code == 200, r.text
    actor, method, broker_path, sent = broker[-1]
    assert (actor, method) == ('u1', 'POST')
    want = 'b' * 32 if action == 'calendar.events' else 'a' * 32
    assert broker_path == f'/api/connections/{want}/read'
    assert sent['action'] == action
    assert 'connection_id' not in sent


def test_the_inbox_is_the_inbox_query(client, broker):
    client.post('/api/connections/gmail/inbox', headers=HUMAN, json={'limit': 3})
    assert broker[-1][3]['query'] == 'in:inbox'


@pytest.mark.parametrize('path,payload', [
    ('/api/connections/request', {'provider': 'google_email'}),
    ('/api/connections/gmail/search', {'query': 'x'}),
    ('/api/connections/gmail/read', {'message_id': 'm1'}),
    ('/api/connections/gmail/draft', {'recipient': 'a@b.c', 'subject': 's', 'body': 'b',
                                      'request_id': 'req-0001'}),
    ('/api/connections/service/call', {'connection_id': 'c' * 32}),
    ('/api/onboarding/research', {'action': 'status'}),
])
def test_a_worker_without_a_person_is_refused_before_the_broker(client, broker, path, payload):
    before = len(broker)
    r = client.post(path, headers=WORKER, json=payload)
    assert r.status_code == 403
    assert r.json()['detail']['reason'] == 'human_session_required'
    assert len(broker) == before


def test_unknown_arguments_are_refused_not_dropped(client):
    assert client.post('/api/connections/gmail/search', headers=HUMAN,
                       json={'query': 'x', 'token': 'leak'}).status_code == 422


def test_every_tool_route_publishes_named_body_fields(client):
    spec = client.get('/openapi.json').json()
    for path in ('/api/connections/request', '/api/connections/gmail/search', '/api/connections/gmail/read',
                 '/api/connections/gmail/thread', '/api/connections/gmail/inbox',
                 '/api/connections/calendar/events', '/api/connections/gmail/draft',
                 '/api/connections/service/call', '/api/onboarding/research'):
        ref = spec['paths'][path]['post']['requestBody']['content']['application/json']['schema']['$ref']
        assert spec['components']['schemas'][ref.rsplit('/', 1)[-1]]['properties'], path
