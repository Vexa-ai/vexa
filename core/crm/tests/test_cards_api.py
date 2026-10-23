"""The card routes over HTTP (desktop D2): the switch, the tool surface and who may do what."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.api import Identity, create_app
from crm.bootstrap import bootstrap_org
from crm.store import Store

CARD_ROUTES = ['/card/list', '/card/propose', '/card/update/propose', '/card/read', '/card/create',
               '/card/update', '/card/members', '/card/membership']
CARD_TOOLS = {'card_list', 'card_propose', 'card_update_propose'}

ANSWERS = {
    'me': {'user_id': '7', 'credential_class': 'session', 'workspaces': ['sales'], 'writable_workspaces': ['sales']},
    'bo': {'user_id': '8', 'credential_class': 'session', 'workspaces': ['sales'], 'writable_workspaces': ['sales']},
    'vxd_watched': {'user_id': '7', 'workspaces': ['sales'],
                    'delegation': {'regime': 'human', 'shared': False, 'unit': 'pchat-1', 'workspaces': ['sales']}},
    'vxd_routine': {'user_id': '7', 'workspaces': ['sales'],
                    'delegation': {'regime': 'autonomous', 'shared': False, 'unit': 'flow-1', 'workspaces': ['sales']}},
}


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    s.metadata.create_all(engine)
    bootstrap_org(engine, 'one', 'One')
    return Store(engine)


def client_for(store):
    def resolve(request):
        token = json.loads(request.content)['token']
        return httpx.Response(200, json=ANSWERS[token]) if token in ANSWERS else httpx.Response(401)
    return TestClient(create_app(store, Identity('http://identity', 'internal-test-only', httpx.MockTransport(resolve)), 'one'))


def as_(token):
    # `x-vexa-taint: none` is what the edge forwards for a session that read nothing private (D6)
    return {'Authorization': f'Bearer {token}', 'x-trace-id': 'trace-' + token, 'x-vexa-taint': 'none'}


def test_a_person_makes_moves_and_shares_a_card(store):
    client = client_for(store)
    made = client.post('/card/create', json={'title': 'Renewal', 'workspace': 'sales', 'about': 'chat:pchat-1'},
                       headers=as_('me'))
    assert made.status_code == 200, made.text
    card = made.json()['card']
    moved = client.post('/card/update', json={'card': card['id'], 'expected_revision': 1, 'transition': 'Doing'},
                        headers=as_('me'))
    assert moved.status_code == 200 and moved.json()['card']['state'] == 'Doing'
    bad = client.post('/card/update', json={'card': card['id'], 'expected_revision': 2, 'transition': 'Shipped'},
                      headers=as_('me'))
    assert bad.status_code == 400
    assert client.post('/card/members', json={'card': card['id'], 'subject': '9', 'add': True},
                       headers=as_('me')).status_code == 200
    read = client.post('/card/read', json={'card': card['id']}, headers=as_('bo'))
    assert read.status_code == 200 and [a['what'] for a in read.json()['activity']][:1] == ['Moved to Doing']


def test_an_agent_proposes_and_the_person_accepts_with_crm_review(store):
    client = client_for(store)
    refused = client.post('/card/create', json={'title': 'x'}, headers=as_('vxd_watched'))
    assert refused.status_code == 403 and refused.json()['reason'] == 'propose_instead'
    p = client.post('/card/propose', json={'title': 'Send the deck', 'workspace': 'sales',
                                           'reason': 'promised on the call'}, headers=as_('vxd_routine'))
    assert p.status_code == 200 and p.json()['status'] == 'proposed'
    listed = client.post('/card/list', json={'workspace': 'sales'}, headers=as_('bo')).json()
    assert listed['cards'] == [] and [x['title'] for x in listed['proposals']] == ['Send the deck']
    agent_review = client.post('/review', json={'proposal_id': p.json()['proposal_id'], 'accept': True},
                               headers=as_('vxd_watched'))
    assert agent_review.status_code == 403 and agent_review.json()['reason'] == 'session_required'   # D13: refused at the door
    ok = client.post('/review', json={'proposal_id': p.json()['proposal_id'], 'accept': True}, headers=as_('bo'))
    assert ok.status_code == 200 and ok.json()['status'] == 'saved'
    card_id = ok.json()['record_id']
    stage = client.post('/card/update/propose', json={'card': card_id, 'expected_revision': 1, 'transition': 'Done',
                                                       'reason': 'shipped'}, headers=as_('vxd_watched'))
    assert stage.status_code == 200 and stage.json()['status'] == 'proposed'
    desk = client.post('/desk/read', json={}, headers=as_('bo')).json()
    assert [c['title'] for c in desk['cards']] == ['Send the deck']
    assert [x['kind'] for x in desk['card_proposals']] == ['change']


def test_cards_off_refuse_and_leave_the_manifest(store, monkeypatch):
    monkeypatch.setenv('VEXA_FEATURES', 'crm,desktop')
    client = client_for(store)
    for route in CARD_ROUTES:
        r = client.post(route, json={}, headers=as_('me'))
        assert r.status_code == 403 and r.json()['detail']['feature'] == 'desktop_cards', route
    names = {t['name'] for t in client.get('/.well-known/mcp-tools.json').json()['tools']}
    assert not names & CARD_TOOLS and 'desktop_read' in names
    assert 'cards' not in client.post('/desk/read', json={}, headers=as_('me')).json()


def test_the_card_tools_are_in_the_manifest_with_their_routes(store):
    client = client_for(store)
    tools = {t['name']: t for t in client.get('/.well-known/mcp-tools.json').json()['tools']}
    assert CARD_TOOLS <= set(tools)
    assert tools['card_propose']['route'] == {'method': 'POST', 'path': '/card/propose'}
    spec = client.get('/openapi.json').json()
    ops = {op['operationId'] for path in spec['paths'].values() for op in path.values() if 'operationId' in op}
    assert CARD_TOOLS <= ops
    # the person-only verbs are routes, never tools
    assert not {'card_create', 'card_update', 'card_members', 'card_membership', 'card_read'} & set(tools)
