"""DENY TESTS FROM THE 2026-10-17 SECURITY VALIDATION (private review).

Each test asserts that an access is REFUSED or a fact is WITHHELD. A test that fails because the
protection is missing is marked ``xfail(strict=True)`` with its finding id, so the suite stays green
while the gap is open and turns red the moment a fix lands (then drop the marker). Offline: SQLite
and an in-process identity stub, no containers.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.api import Identity, create_app
from crm.bootstrap import bootstrap_org
from crm.caller import Caller, from_validate
from crm.card_ops import Cards
from crm.store import CRMError, Store

ORG = 'one'
ADMIN = Caller(subject='1', email='admin@acme.test', is_admin=True)
REVIEW = 'security-validation-2026-10-17'


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


def _company(store, *workspaces, members=None):
    """A company bound to ``workspaces``; ``team``'s roster is the owner ('2') and a member ('3')."""
    rid = store.create(ORG, ADMIN, 'Company', {'Name': 'Globex', 'Domain': 'globex.test', 'Stage': 'Negotiation',
                                               'Owner': 'p-7'}, narrative='private notes', reason='fixture',
                       idempotency_key='co-globex', match=['domain:globex.test', 'email:ceo@globex.test'])['record_id']
    for w in workspaces:
        store.apply_workspace_snapshot(ORG, w, 'c1', '2026-10-17T10:00:00Z', [],
                                       members or [{'subject': '2', 'role': 'owner'},
                                                   {'subject': '3', 'role': 'contributor'}])
        store.bind(ORG, ADMIN, w, rid)
    return rid


def _room_body(*, person_is_admin=True, workspaces=('team',)):
    """identity's answer for a room turn: the workspace agent's shared delegation, in-org audience."""
    return {'user_id': 2, 'email': 'owner@acme.test', 'scopes': ['bot', 'tx'], 'is_admin': False,
            'person_is_admin': person_is_admin, 'workspaces': list(workspaces),
            'writable_workspaces': list(workspaces),
            'delegation': {'regime': 'human', 'workspaces': list(workspaces), 'shared': True, 'unit': 'room-1'}}


def _room(**kw) -> Caller:
    return from_validate('vxd_room.y.z', _room_body(**kw))


# ── C2: a room turn reads with the owner's rights, up to admin ──────────────────────────────────
def test_shared_delegated_caller_is_never_admin(store):
    room = _room(person_is_admin=True)
    assert room.is_admin is False, 'a shared (room) delegation must never be a CRM admin'
    with store.engine.connect() as c:
        assert not store._policy(c, ORG, room).get('admin')


def test_room_crm_read_returns_only_shared_fields(store):
    rid = _company(store, 'team')
    # member '3' of the room may not read Stage on a Company
    with store.engine.begin() as c:
        c.execute(s.policies.insert().values(org_id=ORG, subject_id='3', policy={'active': True, 'objects': {
            'Company': {'read': 'none', 'read_fields': ['Name', 'Domain'], 'write': 'none'}}}))
    rec = store.get(ORG, _room(person_is_admin=True), rid)
    assert 'Stage' not in rec['fields'], f"room read leaked a field a member may not read: {sorted(rec['fields'])}"
    assert 'match_ids' not in rec, 'room read leaked match_ids (emails)'
    assert rec['narrative'] is None


def test_room_crm_read_bound_to_lists_only_ceiling_workspaces(store):
    rid = _company(store, 'team', 'board-private')
    rec = store.get(ORG, _room(person_is_admin=True), rid)
    assert rec['bound_to'] == ['team'], f"bound_to named workspaces outside the ceiling: {rec['bound_to']}"


def test_room_crm_read_bound_to_lists_only_ceiling_workspaces_non_admin_owner(store):
    """Control: without the admin flag the ceiling already narrows bound_to (store.py:311)."""
    rid = _company(store, 'team', 'board-private')
    assert store.get(ORG, _room(person_is_admin=False), rid)['bound_to'] == ['team']


# ── HTTP harness: identity answers per bearer ───────────────────────────────────────────────────
ANSWERS = {
    # D13: the terminal's sign-in is its own credential class; `browser` in the scopes proves nothing.
    'browser-session': {'user_id': '8', 'email': 'bo@acme.test', 'scopes': ['bot', 'tx', 'browser'], 'is_admin': False,
                        'workspaces': ['sales'], 'writable_workspaces': ['sales'], 'credential_class': 'session'},
    'vxa_browserkey': {'user_id': '8', 'email': 'bo@acme.test', 'scopes': ['bot', 'tx', 'browser'], 'is_admin': False,
                       'workspaces': ['sales'], 'writable_workspaces': ['sales'], 'credential_class': 'key'},
    'vxa_botkey': {'user_id': '8', 'email': 'bo@acme.test', 'scopes': ['bot', 'tx'], 'is_admin': False,
                   'workspaces': ['sales'], 'writable_workspaces': ['sales']},
    'vxd_room': _room_body(person_is_admin=False),
    'vxd_watched': {'user_id': '7', 'workspaces': ['sales'], 'scopes': ['bot', 'tx'],
                    'delegation': {'regime': 'human', 'shared': False, 'unit': 'pchat-1', 'workspaces': ['sales']}},
}


def client_for(store):
    def resolve(request):
        token = json.loads(request.content)['token']
        return httpx.Response(200, json=ANSWERS[token]) if token in ANSWERS else httpx.Response(401)
    return TestClient(create_app(store, Identity('http://identity', 'internal-test-only',
                                                 httpx.MockTransport(resolve)), ORG))


def as_(token):
    # `x-vexa-taint: none` is what the edge forwards for a session that read nothing private (D6)
    return {'Authorization': f'Bearer {token}', 'x-trace-id': 'trace-' + token, 'x-vexa-taint': 'none'}


def _proposed_card(client):
    p = client.post('/card/propose', json={'title': 'Send the deck', 'workspace': 'sales', 'reason': 'promised'},
                    headers=as_('vxd_watched'))
    assert p.status_code == 200, p.text
    return p.json()['proposal_id']


# ── H3 (crm half): an API key is treated as a person ────────────────────────────────────────────
# Was xfail (H3 / deny test 12); passes since s5-cards (PRD 0.13.4 D13): review and sign take the session
# credential only — every API key is refused, a browser-scoped one included, and so is a delegation.
@pytest.mark.parametrize('token', ['vxa_botkey', 'vxa_browserkey', 'vxd_watched'])
def test_api_key_cannot_review_card(store, token):
    client = client_for(store)
    r = client.post('/review', json={'proposal_id': _proposed_card(client), 'accept': True}, headers=as_(token))
    assert r.status_code == 403, f'{token} reviewed a proposal: {r.status_code} {r.text}'
    assert r.json()['reason'] == 'session_required' or token.startswith('vxd_')


@pytest.mark.parametrize('route', ['/card/decide', '/card/nonce', '/card/seen'])
@pytest.mark.parametrize('token', ['vxa_botkey', 'vxa_browserkey', 'vxd_watched'])
def test_api_key_cannot_sign_a_card(store, token, route):
    """Deny test 12, the signing half: no API key and no delegation reaches a decision, its nonce or seen."""
    client = client_for(store)
    body = {'card': 'any'} if route != '/card/decide' else {
        'card': 'any', 'action': 'acknowledge', 'nonce': 'n' * 20, 'content_hash': '0' * 64}
    r = client.post(route, json=body, headers=as_(token))
    assert r.status_code == 403 and r.json()['reason'] == 'session_required', f'{token} on {route}: {r.status_code} {r.text}'


def test_browser_session_may_review_card(store):
    """Control for H3: the browser-session credential is the one that should pass."""
    client = client_for(store)
    r = client.post('/review', json={'proposal_id': _proposed_card(client), 'accept': True},
                    headers=as_('browser-session'))
    assert r.status_code == 200 and r.json()['status'] == 'saved', r.text


# ── deny test 17: a viewer who sees a locked field may not sign the card ────────────────────────
def _person(uid, *workspaces):
    return Caller(subject=uid, email=f'u{uid}@acme.test', workspaces=workspaces, writable=workspaces)


# Was xfail (deny test 17); passes since s5-cards (PRD 0.13.4 D13): accepting is signing.
def test_card_with_locked_field_cannot_be_signed_by_that_viewer(store):
    cards = Cards(store)
    ana, bo = _person('7', 'sales'), _person('8', 'sales')
    with store.engine.begin() as c:
        c.execute(s.policies.insert().values(org_id=ORG, subject_id='8', policy={'active': True, 'objects': {
            'Card': {'read': 'none', 'read_fields': ['Title', 'State', 'Face'], 'write': 'none',
                     'write_fields': ['State']}}}))
    card = cards.create(ORG, ana, title='Renewal', workspace='sales', fields={'Summary': 'margin is 12%'},
                        face=[{'type': 'text', 'field': 'Summary', 'label': 'Summary'}])['card']
    assert {'type': 'locked', 'label': 'Summary'} in cards.read(ORG, bo, card['id'])['face']   # precondition
    agent = Caller(subject='7', workspaces=('sales',), writable=('sales',), delegated=True, regime='human',
                   session='pchat-1')
    p = cards.update_propose(ORG, agent, card['id'], card['revision'], transition='Done', reason='agreed')
    refused = False
    try:
        store.review(ORG, bo, p['proposal_id'], True)
    except CRMError:
        refused = True
    assert refused, 'a viewer for whom a face field is locked signed (reviewed) the card'


# ── H9 / deny test 18: room search and profile reads are audited ────────────────────────────────
def _audit(store, op):
    with store.engine.connect() as c:
        return [dict(r) for r in c.execute(select(s.audit).where(s.audit.c.op == op)).mappings()]


def test_room_crm_search_audits_record_ids_and_on_behalf_of(store):
    rid = _company(store, 'team')
    client = client_for(store)
    r = client.post('/search', json={'object_type': 'Company'}, headers=as_('vxd_room'))
    assert r.status_code == 200 and [x['id'] for x in r.json()['records']] == [rid], r.text
    rows = _audit(store, 'search')
    assert rows and rows[-1]['caller'] == 'agent-shared'
    assert rid in rows[-1]['record_id'], f"search audit row does not name the returned record: {rows[-1]['record_id']!r}"
    assert {'on_behalf_of', 'workspace'} <= set(s.audit.c.keys()), 'audit has no on_behalf_of / workspace column'


def test_profile_read_is_audited(store, monkeypatch):
    monkeypatch.setenv('VEXA_FEATURES', 'all,public_profiles')
    rid = _company(store, 'sales', members=[{'subject': '8', 'role': 'contributor'}])
    client = client_for(store)
    r = client.post('/profile/read', json={'record_id': rid}, headers=as_('browser-session'))
    assert r.status_code == 200, r.text
    with store.engine.connect() as c:
        rows = [dict(x) for x in c.execute(select(s.audit).where(s.audit.c.record_id == rid)).mappings()]
    assert any('profile' in row['op'] for row in rows), f'no audit row for /profile/read: {[x["op"] for x in rows]}'


# ── deny test 36 (PRD 0.13.4 D2 + D4): a workspace principal never falls to the org default ─────
def _principal_body(*, workspaces=('team',)):
    """identity's answer for a room turn under VEXA_ROOM_WS_PRINCIPAL: the WORKSPACE's own numeric
    principal (no person's account), `shared` deliberately absent — the kind alone must make it shared."""
    return {'user_id': 900, 'email': 'ws-900@principal.invalid', 'scopes': ['bot', 'tx'], 'is_admin': True,
            'person_is_admin': True, 'workspaces': list(workspaces), 'writable_workspaces': list(workspaces),
            'delegation': {'regime': 'human', 'workspaces': list(workspaces), 'unit': 'room-1',
                           'principal_kind': 'workspace', 'principal_workspace': 'team',
                           'on_behalf_of': '2', 'bills_to': 2}}


def test_workspace_principal_gets_members_intersection_never_the_org_default(store):
    rid = _company(store, 'team')
    with store.engine.begin() as c:
        # the org default would grant every field of every Company …
        c.execute(s.orgs.update().where(s.orgs.c.org_id == ORG).values(default_policy={
            'active': True, 'objects': {'Company': {'read': 'all', 'read_fields': ['*'], 'write': 'all'}}}))
        # … but member '3' of the room may not read Stage; the owner '2' holds an explicit policy
        c.execute(s.policies.insert().values(org_id=ORG, subject_id='3', policy={'active': True, 'objects': {
            'Company': {'read': 'none', 'read_fields': ['Name', 'Domain'], 'write': 'none'}}}))
        c.execute(s.policies.insert().values(org_id=ORG, subject_id='2', policy={'active': True, 'objects': {
            'Company': {'read': 'all', 'read_fields': ['*'], 'write': 'all'}}}))
    principal = from_validate('vxd_room.y.z', _principal_body())
    assert principal.shared is True and principal.is_admin is False
    with store.engine.connect() as c:
        policy = store._policy(c, ORG, principal)
    assert not policy.get('admin') and policy.get('objects') == {}, 'a principal took the org default policy'
    rec = store.get(ORG, principal, rid)
    assert 'Stage' not in rec['fields'], f"principal read past the members' intersection: {sorted(rec['fields'])}"
    assert rec['fields'].get('Name') == 'Globex'
    # with the org default switched OFF the room still reads what its members share: it is not that policy
    with store.engine.begin() as c:
        c.execute(s.orgs.update().where(s.orgs.c.org_id == ORG).values(default_policy={'active': False}))
    assert store.get(ORG, principal, rid)['fields'].get('Name') == 'Globex'
