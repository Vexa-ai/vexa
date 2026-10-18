"""The desk's five routes over HTTP: the feature switch, the typed refusals and the tool surface."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from crm import features, schema as s
from crm.api import Identity, create_app
from crm.store import Store

DESK_ROUTES = ['/desk/read', '/desk/arrange', '/desk/arrange/propose', '/desk/proposal', '/desk/undo']
DESK_TOOLS = {'desktop_read', 'desktop_arrange', 'desktop_arrange_propose'}

ANSWERS = {
    'me': {'user_id': '7', 'workspaces': ['sales']},
    'vxd_watched': {'user_id': '7', 'workspaces': ['sales'],
                    'delegation': {'regime': 'human', 'shared': False, 'unit': 'pchat-1', 'workspaces': ['sales']}},
    'vxd_routine': {'user_id': '7', 'workspaces': ['sales'],
                    'delegation': {'regime': 'autonomous', 'shared': False, 'unit': 'flow-1', 'workspaces': ['sales']}},
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


def test_the_person_reads_and_arranges_their_desk(store):
    client = client_for(store)
    d = client.post('/desk/read', json={}, headers=as_('me'))
    assert d.status_code == 200 and d.json()['version'] == 0
    r = client.post('/desk/arrange', json={'ops': [{'op': 'section_top', 'section': 'agents'}], 'base_version': 0},
                    headers=as_('me'))
    assert r.status_code == 200 and r.json() == {'status': 'applied', 'version': 1, 'undo_version': 0,
                                                 'summary': 'Moved Agents above Workspaces'}
    u = client.post('/desk/undo', json={'version': 0, 'base_version': 1}, headers=as_('me'))
    assert u.status_code == 200 and u.json()['version'] == 2
    with store.engine.connect() as c:
        ops = [row.op for row in c.execute(select(s.audit.c.op).where(s.audit.c.outcome == 'ok'))]
    assert {'desk.read', 'desk.arrange', 'desk.undo'} <= set(ops)


def test_the_typed_refusals_are_the_bodies_a_worker_reads(store):
    client = client_for(store)
    body = {'ops': [{'op': 'hide', 'section': 'files'}], 'base_version': 0}
    r = client.post('/desk/arrange', json=body, headers=as_('vxd_routine'))
    assert r.status_code == 403 and r.json()['reason'] == 'propose_instead'
    assert client.post('/desk/arrange', json=body, headers=as_('vxd_watched')).status_code == 200
    r = client.post('/desk/arrange', json=body, headers=as_('me'))
    assert r.status_code == 409 and r.json()['reason'] == 'desk_changed' and r.json()['version'] == 1
    r = client.post('/desk/arrange', json={'ops': [{'op': 'place', 'ref': 'file:finance/a.md'}], 'base_version': 1},
                    headers=as_('me'))
    assert r.status_code == 403 and r.json()['reason'] == 'not_readable'
    r = client.post('/desk/arrange', json={'ops': [{'op': 'place', 'ref': 'card:x'}], 'base_version': 1},
                    headers=as_('me'))
    # cards are served on a dev stack (desktop D2): a card this person cannot read is not placed;
    # with `desktop_cards` off it is ref_kind_not_enabled (tests/test_cards_api.py)
    assert r.status_code == 403 and r.json()['reason'] == 'not_readable'
    r = client.post('/desk/arrange', json={'ops': [{'op': 'hide', 'section': 'files'}, {'op': 'nope'}], 'base_version': 1},
                    headers=as_('me'))
    assert r.status_code == 422 and 'ops[1]' in r.json()['detail']


def test_a_routine_proposes_and_only_the_person_applies(store):
    client = client_for(store)
    p = client.post('/desk/arrange/propose', json={'ops': [{'op': 'hide', 'section': 'files'}], 'reason': 'tidy'},
                    headers=as_('vxd_routine'))
    assert p.status_code == 200 and p.json()['status'] == 'proposed'
    pid = p.json()['proposal_id']
    review = {'proposal_id': pid, 'apply': True, 'base_version': 0}
    r = client.post('/desk/proposal', json=review, headers=as_('vxd_watched'))
    assert r.status_code == 403 and r.json()['reason'] == 'person_confirmation_required'
    r = client.post('/desk/proposal', json=review, headers=as_('me'))
    assert r.status_code == 200 and r.json()['status'] == 'applied'


def test_bodies_are_closed_and_the_org_is_never_an_argument(store):
    client = client_for(store)
    assert client.post('/desk/read', json={'org_id': 'two'}, headers=as_('me')).status_code == 422
    assert client.post('/desk/arrange', json={'ops': [], 'base_version': 0}, headers=as_('me')).status_code == 422
    assert client.post('/desk/arrange', json={'ops': [{'op': 'hide', 'section': 'files'}], 'base_version': 0,
                                              'person': '8'}, headers=as_('me')).status_code == 422


def test_org_isolation_through_the_service_binding(store):
    one, two = client_for(store, 'one'), client_for(store, 'two')
    one.post('/desk/arrange', json={'ops': [{'op': 'place', 'ref': 'agent:r1'}], 'base_version': 0}, headers=as_('me'))
    assert two.post('/desk/read', json={}, headers=as_('me')).json()['placements'] == []
    assert one.post('/desk/read', json={}, headers=as_('me')).json()['placements'][0]['ref'] == 'agent:r1'


def test_three_desk_tools_and_review_and_undo_are_not_tools(store):
    client = client_for(store)
    manifest = client.get('/.well-known/mcp-tools.json').json()
    names = {t['name'] for t in manifest['tools']}
    assert DESK_TOOLS <= names
    spec = client.get('/openapi.json').json()
    for t in manifest['tools']:
        if t['name'] in DESK_TOOLS:
            op = spec['paths'][t['route']['path']]['post']
            assert op['operationId'] == t['name'] and t['auth'] == 'subject'
            assert t['requires'] == ['identity', 'crm']
            assert 'Undo' in op['description'] or t['name'] != 'desktop_arrange'
            assert set(t['arguments']) <= set(_body_fields(spec, op))
    routes = {t['route']['path'] for t in manifest['tools']}
    assert '/desk/proposal' not in routes and '/desk/undo' not in routes


def _body_fields(spec, op):
    ref = op['requestBody']['content']['application/json']['schema']['$ref'].split('/')[-1]
    return spec['components']['schemas'][ref].get('properties', {}).keys()


@pytest.mark.parametrize('raw', ['crm', 'all,-desktop', 'desktop'])
def test_desktop_off_refuses_the_desk_and_drops_its_tools(store, monkeypatch, raw):
    monkeypatch.setenv('VEXA_FEATURES', raw)
    client = client_for(store)
    for path in DESK_ROUTES:
        r = client.post(path, json={}, headers=as_('me'))
        assert r.status_code == 403
        assert r.json() == {'detail': features.refusal('desktop', features.DESKTOP_SUMMARY)}
    names = {t['name'] for t in client.get('/.well-known/mcp-tools.json').json()['tools']}
    assert not names & DESK_TOOLS
    if features.switched_on('crm'):
        assert 'crm_read' in names                                 # the rest of the CRM is untouched
        assert 'feature_disabled' not in client.post('/describe', json={}, headers=as_('me')).text


@pytest.mark.parametrize('raw', ['all', 'crm,desktop'])
def test_desktop_on_serves_the_desk(store, monkeypatch, raw):
    monkeypatch.setenv('VEXA_FEATURES', raw)
    client = client_for(store)
    assert client.post('/desk/read', json={}, headers=as_('me')).status_code == 200
    names = {t['name'] for t in client.get('/.well-known/mcp-tools.json').json()['tools']}
    assert DESK_TOOLS <= names


def test_the_app_applies_the_company_limits_it_is_given(store):
    from crm.policies import DEFAULTS
    def resolve(request):
        return httpx.Response(200, json=ANSWERS['me'])
    app = create_app(store, Identity('http://identity', 'internal-test-only', httpx.MockTransport(resolve)), 'one',
                     limits=lambda: dict(DEFAULTS, desk_placements_max=1))
    client = TestClient(app)
    ok = client.post('/desk/arrange', json={'ops': [{'op': 'place', 'ref': 'agent:a'}], 'base_version': 0},
                     headers=as_('me'))
    assert ok.status_code == 200, ok.text
    full = client.post('/desk/arrange', json={'ops': [{'op': 'place', 'ref': 'agent:b'}], 'base_version': 1},
                       headers=as_('me'))
    assert full.status_code == 409 and full.json()['reason'] == 'desk_placements_max'


def test_the_configured_app_reads_policy_through_the_agent_door(monkeypatch):
    from crm import api
    seen = {}
    monkeypatch.setattr(api, 'Policies', lambda url, secret: seen.update(url=url, secret=secret) or 'P')
    assert api.company_limits({'CRM_AGENT_API_URL': 'http://agent-api:8100', 'INTERNAL_API_SECRET': 'x'}) == 'P'
    assert seen == {'url': 'http://agent-api:8100', 'secret': 'x'}
