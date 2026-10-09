"""The Connections routes the assembled MCP serves as tools (core/agent/mcp.tools.v1.json).

Each tool is one typed route: the broker is faked at `call_broker`, so these pin what each route
asks the broker for, what it returns to the agent (metadata, never a credential), and that every
verb needing a person in the loop refuses a worker dispatched without one.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import route_policy
from control_plane.routers import connections

READY = [{'id': 'a' * 32, 'provider': 'google_email', 'status': 'ready', 'label': 'Gmail',
          'access_token': 'never-shown'},
         {'id': 'b' * 32, 'provider': 'google_calendar', 'status': 'ready', 'label': ''}]


@pytest.fixture
def broker(monkeypatch):
    calls = []

    def fake(actor, method, path, payload=None, *, identity):
        assert identity == SIGNED, 'every broker call forwards the gateway signature unchanged'
        calls.append((actor, method, path, payload))
        if path == '/api/connections':
            return {'connections': READY}
        return {'ok': True, 'path': path}

    monkeypatch.setattr(connections, 'call_broker', fake)
    return calls


def _app() -> FastAPI:
    """The router under agent-api's own person gate — the one place a `person` verb is refused."""
    app = FastAPI(dependencies=[route_policy.PERSON_GATE])
    app.include_router(connections.build(subject_of=lambda r: r.headers['x-user-id']))
    return app


@pytest.fixture
def client(broker):
    return TestClient(_app())


SIGNED = 'v1.signed-by-the-gateway.for-u1'
HUMAN = {'X-User-Id': 'u1', 'X-Vexa-Identity': SIGNED}
WORKER = {'X-User-Id': 'u1', 'X-User-Regime': 'autonomous', 'X-Vexa-Identity': SIGNED}
INTERNAL_TIER = {'X-User-Id': 'u1'}   # named over the internal tier: no gateway signature


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


@pytest.mark.parametrize('method,path,payload', [
    ('GET', '/api/connections', None),
    ('POST', '/api/connections/request', {'provider': 'google_email'}),
    ('POST', '/api/connections/gmail/search', {'query': 'x'}),
    ('POST', '/api/connections/gmail/draft', {'recipient': 'a@b.c', 'subject': 's', 'body': 'b',
                                              'request_id': 'req-0001'}),
    ('POST', '/api/connections/service/call', {'connection_id': 'c' * 32}),
    ('POST', '/api/onboarding/research', {'action': 'status'}),
])
def test_a_caller_without_the_gateway_signature_never_reaches_the_broker(client, broker, method, path, payload):
    """The broker acts for a person only on the gateway's signature (gateway-identity.v1). A caller
    that named the person over the internal tier has none, so agent-api refuses it here with a
    sentence instead of letting the broker refuse it as an opaque fault."""
    before = len(broker)
    r = client.request(method, path, headers=INTERNAL_TIER, json=payload)
    assert r.status_code == 403
    assert 'signed in through the gateway' in r.json()['detail']
    assert len(broker) == before


# ── a setup proposal: every key published, any other refused BY NAME ─────────────────────────────
TELEGRAM = {'endpoint': 'https://api.telegram.org/bot{secret}/sendMessage', 'scheme': 'telegram',
            'method': 'POST', 'secret_label': 'Bot token',
            'fields': [{'name': 'chat_id', 'label': 'Chat ID', 'location': 'body'}]}


def test_a_setup_proposal_reaches_the_broker_as_written(client, monkeypatch):
    sent = []

    def fake(actor, method, path, payload=None, *, identity):
        sent.append((actor, method, path, payload))
        if path == '/api/connections':
            return {'connections': []}
        return {'connection_id': 'c' * 32, 'status': 'awaiting_user'}

    monkeypatch.setattr(connections, 'call_broker', fake)
    broker = sent
    r = client.post('/api/connections/request', headers=HUMAN,
                    json={'provider': 'custom_secret', 'label': 'Telegram', 'setup': TELEGRAM})
    assert r.status_code == 200, r.text
    prepared = [c for c in broker if c[2].endswith('/prepare')]
    assert prepared and prepared[-1][3] == {'setup': TELEGRAM}


@pytest.mark.parametrize('setup,named', [
    ({**TELEGRAM, 'service': 'Telegram'}, 'setup.service is not a field here (allowed: oauth, '
                                          'documentation_url, endpoint, header, scheme, method, '
                                          'secret_label, fields)'),
    ({**TELEGRAM, 'fields': [{'name': 'chat_id', 'label': 'Chat ID', 'value': '12345'}]},
     'setup.fields.0.value is not a field here (allowed: name, label, location)'),
    ({**TELEGRAM, 'scheme': 'basic'}, 'setup.scheme:'),
])
def test_a_key_outside_the_setup_is_refused_by_name_and_its_value_is_never_echoed(client, broker,
                                                                                   setup, named):
    r = client.post('/api/connections/request', headers=HUMAN,
                    json={'provider': 'custom_secret', 'label': 'Telegram', 'setup': setup})
    assert r.status_code == 422
    detail = r.json()['detail']
    assert isinstance(detail, str) and named in detail, detail
    assert '12345' not in detail and "'Telegram'" not in detail
    assert not [c for c in broker if c[2].endswith('/prepare')], 'nothing reached the broker'


def test_the_published_setup_is_closed_and_the_description_names_label_not_service():
    app = FastAPI()
    app.include_router(connections.build(subject_of=lambda r: 'u1'))
    spec = app.openapi()
    setup = spec['components']['schemas']['ConnectionRequest']['properties']['setup']
    ref = next(b['$ref'] for b in setup['anyOf'] if '$ref' in b)
    model = spec['components']['schemas'][ref.rsplit('/', 1)[-1]]
    assert model['additionalProperties'] is False
    assert set(model['properties']) == {'oauth', 'documentation_url', 'endpoint', 'header', 'scheme',
                                        'method', 'secret_label', 'fields'}
    text = spec['paths']['/api/connections/request']['post']['description']
    assert 'name the service in label' in text and 'provide a service label' not in text


def test_an_outage_choosing_the_mailbox_is_not_reported_as_a_missing_one(monkeypatch):
    """The draft route turns "no single ready mailbox" into a 409 that says to choose one. A broker
    outage on the same lookup keeps its own status, so it never reads as the person's mistake."""
    from fastapi import HTTPException

    def down(actor, method, path, payload=None, *, identity):
        raise HTTPException(503, 'Credential store unavailable — an outage, not an authorization problem')
    monkeypatch.setattr(connections, 'call_broker', down)
    r = TestClient(_app()).post('/api/connections/gmail/draft', headers=HUMAN,
                             json={'request_id': 'req-00000001', 'recipient': 'a@b.example', 'subject': 's', 'body': 'b'})
    assert r.status_code == 503 and 'outage' in r.json()['detail']
