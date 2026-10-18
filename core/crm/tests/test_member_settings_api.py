"""Workspace settings over HTTP: two routes under /desk/ (the `desktop` switch), typed refusals, audit
and the two agent tools."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from crm import features, schema as s
from crm.api import Identity, create_app
from crm.store import Store

ROUTES = ['/desk/workspaces/read', '/desk/workspaces/set']
TOOLS = {'workspace_settings_read': '/desk/workspaces/read', 'workspace_settings_set': '/desk/workspaces/set'}

ANSWERS = {
    'me': {'user_id': '7', 'workspaces': ['sales']},
    'other': {'user_id': '8', 'workspaces': ['sales']},
    'vxd_watched': {'user_id': '7', 'workspaces': ['sales'],
                    'delegation': {'regime': 'human', 'shared': False, 'unit': 'pchat-1', 'workspaces': ['sales']}},
    'vxd_routine': {'user_id': '7', 'workspaces': ['sales'],
                    'delegation': {'regime': 'autonomous', 'shared': False, 'unit': 'flow-1', 'workspaces': ['sales']}},
    'vxd_shared': {'user_id': '7', 'workspaces': ['sales'],
                   'delegation': {'regime': 'human', 'shared': True, 'unit': 'chat-2', 'workspaces': ['sales']}},
}


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    s.metadata.create_all(engine)
    return Store(engine)


def client_for(store, org='one'):
    def resolve(request):
        token = json.loads(request.content)['token']
        return httpx.Response(200, json=ANSWERS[token]) if token in ANSWERS else httpx.Response(401)
    return TestClient(create_app(store, Identity('http://identity', 'internal-test-only', httpx.MockTransport(resolve)), org))


def as_(token):
    return {'Authorization': f'Bearer {token}', 'x-trace-id': 'trace-' + token}


def test_read_set_read_and_both_are_audited(store):
    client = client_for(store)
    assert client.post('/desk/workspaces/read', json={}, headers=as_('me')).json() == {'workspaces': {}}
    r = client.post('/desk/workspaces/set', json={'workspace': 'growth', 'settings': {'muted': True}}, headers=as_('me'))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['workspace'] == 'growth' and body['settings']['muted'] is True and body['settings']['notify'] == 'all'
    assert body['settings']['updated_at']
    r = client.post('/desk/workspaces/set', json={'workspace': 'growth', 'settings': {'level': 2, 'room_order': ['c1']}},
                    headers=as_('me'))
    assert r.json()['settings']['muted'] is True and r.json()['settings']['level'] == 2
    got = client.post('/desk/workspaces/read', json={}, headers=as_('me')).json()
    assert got == {'workspaces': {'growth': r.json()['settings']}}
    with store.engine.connect() as c:
        ops = [(row.op, row.outcome) for row in c.execute(select(s.audit.c.op, s.audit.c.outcome))]
    assert ('desk.workspaces.read', 'ok') in ops and ('desk.workspaces.set', 'ok') in ops


def test_null_level_clears_it(store):
    client = client_for(store)
    client.post('/desk/workspaces/set', json={'workspace': 'growth', 'settings': {'level': 4}}, headers=as_('me'))
    r = client.post('/desk/workspaces/set', json={'workspace': 'growth', 'settings': {'level': None}}, headers=as_('me'))
    assert r.status_code == 200 and r.json()['settings']['level'] is None


def test_two_people_see_only_their_own(store):
    client = client_for(store)
    client.post('/desk/workspaces/set', json={'workspace': 'growth', 'settings': {'muted': True}}, headers=as_('me'))
    client.post('/desk/workspaces/set', json={'workspace': 'finance', 'settings': {'pinned': True}}, headers=as_('other'))
    assert set(client.post('/desk/workspaces/read', json={}, headers=as_('me')).json()['workspaces']) == {'growth'}
    assert set(client.post('/desk/workspaces/read', json={}, headers=as_('other')).json()['workspaces']) == {'finance'}


@pytest.mark.parametrize('token', ['vxd_routine', 'vxd_shared'])
def test_a_routine_or_a_shared_chat_is_refused_person_only_and_nothing_is_written(store, token):
    client = client_for(store)
    r = client.post('/desk/workspaces/set', json={'workspace': 'growth', 'settings': {'muted': True}}, headers=as_(token))
    assert r.status_code == 403 and r.json()['reason'] == 'person_only' and r.json()['status'] == 'refused'
    with store.engine.connect() as c:
        assert c.execute(select(s.member_settings)).first() is None
        assert ('desk.workspaces.set', 'PersonOnly') in [(x.op, x.outcome) for x in c.execute(select(s.audit))]


def test_the_watched_worker_sets_for_its_person(store):
    client = client_for(store)
    r = client.post('/desk/workspaces/set', json={'workspace': 'growth', 'settings': {'hidden': True}},
                    headers=as_('vxd_watched'))
    assert r.status_code == 200 and r.json()['settings']['hidden'] is True


@pytest.mark.parametrize('body', [
    {'workspace': 'growth', 'settings': {'notify': 'loud'}},
    {'workspace': 'growth', 'settings': {'level': 9}},
    {'workspace': 'growth', 'settings': {'colour': 'red'}},
    {'workspace': 'growth', 'settings': {'updated_at': '2026-01-01'}},
    {'workspace': 'growth', 'settings': {'room_order': [f'c{i}' for i in range(101)]}},
    {'workspace': 'growth', 'settings': {'collapsed': [f's{i}' for i in range(21)]}},
    {'workspace': 'growth', 'settings': {'pinned': 'yes'}},
    {'workspace': 'growth', 'settings': {}},
    {'workspace': '../x', 'settings': {'muted': True}},
    {'workspace': 'growth', 'settings': {'muted': True}, 'person': '8'},
    {'settings': {'muted': True}},
])
def test_invalid_bodies_are_422_and_write_nothing(store, body):
    client = client_for(store)
    r = client.post('/desk/workspaces/set', json=body, headers=as_('me'))
    assert r.status_code == 422, r.text
    with store.engine.connect() as c:
        assert c.execute(select(s.member_settings)).first() is None


def test_read_takes_an_empty_body_only(store):
    client = client_for(store)
    assert client.post('/desk/workspaces/read', json={'person': '8'}, headers=as_('me')).status_code == 422


@pytest.mark.parametrize('raw', ['crm', 'all,-desktop', 'desktop', ''])
def test_desktop_off_refuses_the_routes_and_drops_the_tools(store, monkeypatch, raw):
    monkeypatch.setenv('VEXA_FEATURES', raw)
    client = client_for(store)
    for path in ROUTES:
        r = client.post(path, json={}, headers=as_('me'))
        assert r.status_code == 403
        assert r.json()['detail']['reason'] == 'feature_disabled'
    names = {t['name'] for t in client.get('/.well-known/mcp-tools.json').json()['tools']}
    assert not names & set(TOOLS)


def test_the_two_tools_are_declared_for_an_agent(store):
    client = client_for(store)
    manifest = client.get('/.well-known/mcp-tools.json').json()
    spec = client.get('/openapi.json').json()
    tools = {t['name']: t for t in manifest['tools'] if t['name'] in TOOLS}
    assert {n: t['route']['path'] for n, t in tools.items()} == TOOLS
    for name, t in tools.items():
        op = spec['paths'][t['route']['path']]['post']
        assert op['operationId'] == name and t['auth'] == 'subject' and t['requires'] == ['identity', 'crm']
        ref = op['requestBody']['content']['application/json']['schema']['$ref'].split('/')[-1]
        assert set(t['arguments']) <= set(spec['components']['schemas'][ref].get('properties', {}))
    assert tools['workspace_settings_set']['arguments'] == ['workspace', 'settings']
    assert 'asked' in spec['paths']['/desk/workspaces/set']['post']['description']
