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


# ── A2-b: a CRM writer cannot claim someone else's Person by writing their own address onto it ─────

VICTIM, ATTACKER = 'val@work.test', 'mallory@acme.test'


def _writer(store, subject):
    """A CRM member who may write every Person (not an administrator)."""
    with store.engine.begin() as c:
        c.execute(s.policies.insert().values(org_id=ORG, subject_id=subject, policy={
            'active': True, 'objects': {'Person': {'read': 'all', 'read_fields': ['*'], 'write': 'all',
                                                   'write_fields': ['*'], 'create': True}}}))


def _victim_record(store):
    return store.create(ORG, ADMIN, 'Person', {'Name': 'Val Example', 'Email': VICTIM}, reason='met',
                        idempotency_key='victim', match=['email:' + VICTIM])['record_id']


def _claim_status(store, who, record_id=None):
    try:
        return store.profiles.claim(ORG, who, record_id)
    except CRMError as e:
        return {'refused': getattr(e, 'status', 400)}


def _claimed_by(store, record_id):
    with store.engine.connect() as c:
        return c.execute(select(s.profiles.c.claimed_by).where(
            s.profiles.c.org_id == ORG, s.profiles.c.record_id == record_id)).scalar_one_or_none()


def test_a_writer_who_sets_a_persons_email_to_their_own_address_cannot_claim_it(store):
    """A2-b: the record's ``Email`` field is writable by any writer, so it never makes a claim."""
    victim = _victim_record(store)
    _writer(store, '40')
    mallory = from_validate('t', {'user_id': 40, 'email': ATTACKER, 'email_verified': True,
                                  'scopes': ['bot'], 'is_admin': False})
    rev = store.get(ORG, ADMIN, victim)['revision']
    store.change(ORG, mallory, victim, rev, {'Email': ATTACKER}, reason='typo fix')
    with store.engine.begin() as c:
        assert store.profiles.auto_claim(c, ORG, mallory) is None
    assert _claim_status(store, mallory, victim)['refused'] in (403, 404)
    assert _claimed_by(store, victim) is None


def test_a_writer_who_merges_their_own_person_into_another_cannot_claim_it(store):
    """A2-b through the identifier: a merge carries the writer's ``email:`` identifier onto the
    victim's record; the writer's own merge is not a binding they may claim through."""
    victim = _victim_record(store)
    _writer(store, '40')
    mallory = from_validate('t', {'user_id': 40, 'email': ATTACKER, 'email_verified': True,
                                  'scopes': ['bot'], 'is_admin': False})
    own = store.create(ORG, mallory, 'Person', {'Name': 'M'}, reason='me', idempotency_key='m',
                       match=['email:' + ATTACKER])['record_id']
    store.merge(ORG, mallory, own, victim, reason='duplicate')
    with store.engine.connect() as c:
        assert store.profiles.persons._held(c, ORG, 'email:' + ATTACKER) == victim   # the identifier moved
    with store.engine.begin() as c:
        assert store.profiles.auto_claim(c, ORG, mallory) is None
    assert _claim_status(store, mallory, victim)['refused'] in (403, 404)
    assert _claimed_by(store, victim) is None


def test_the_person_the_org_bound_by_identifier_still_claims_their_record(store):
    """The legitimate first claim: the org's record holds ``email:<address>``; a verified sign-in
    with that address claims it, even after a writer other than the claimant edited or merged it."""
    victim = _victim_record(store)
    _writer(store, '40')
    mallory = from_validate('t', {'user_id': 40, 'email': ATTACKER, 'email_verified': True,
                                  'scopes': ['bot'], 'is_admin': False})
    dup = store.create(ORG, mallory, 'Person', {'Name': 'Val E.'}, reason='dup', idempotency_key='d')['record_id']
    store.merge(ORG, mallory, dup, victim, reason='duplicate')
    val = from_validate('t', {'user_id': 21, 'email': VICTIM, 'email_verified': True,
                              'scopes': ['bot'], 'is_admin': False})
    store.ensure_account(ORG, val)
    with store.engine.begin() as c:
        assert store.profiles.auto_claim(c, ORG, val) == victim
    assert _claimed_by(store, victim) == '21'


# ── A2-c: a CRM merge moves data, never an account ─────────────────────────────────────────────

MALLORY = {'user_id': 40, 'email': ATTACKER, 'email_verified': True, 'scopes': ['bot'], 'is_admin': False}
VAL = {'user_id': 21, 'email': VICTIM, 'email_verified': True, 'scopes': ['bot'], 'is_admin': False}


def _writer_merges_own_person_into_victim(store):
    """The A2-c move: a writer with ``all`` write merges their own sign-in Person into a record the org
    made for someone who has not signed in yet, then that person signs in, claims it and keeps a
    private bio. The merge is refused; the steps after it run either way, so each test below states
    the consequence it denies."""
    victim = _victim_record(store)
    _writer(store, '40')
    mallory = from_validate('t', MALLORY)
    own = store.ensure_account(ORG, mallory)                     # the writer's sign-in Person
    try:
        store.merge(ORG, mallory, own, victim, reason='duplicate')
    except CRMError:
        pass
    val = from_validate('t', VAL)
    store.ensure_account(ORG, val)
    with store.engine.begin() as c:
        assert store.profiles.auto_claim(c, ORG, val) == victim
    store.profiles.edit(ORG, val, {'bio': 'PRIVATE-BIO-7731'})
    return victim, own, mallory, val


def test_a_writer_who_merges_their_account_into_another_person_does_not_resolve_to_them(store):
    victim, own, mallory, _ = _writer_merges_own_person_into_victim(store)
    with store.engine.connect() as c:
        assert store.persons.resolve(c, ORG, '40', ATTACKER)['id'] == own
        assert store.persons.resolve(c, ORG, '40')['id'] == own
    assert store.profiles.mine(ORG, mallory)['fields'] == {}
    assert store.profiles.directory(ORG, [{'subject': '40'}])['40'] is None


def test_a_crm_merge_of_a_sign_in_person_is_refused_and_changes_nothing(store):
    victim = _victim_record(store)
    _writer(store, '40')
    mallory = from_validate('t', MALLORY)
    own = store.ensure_account(ORG, mallory)
    with pytest.raises(CRMError) as refused:
        store.merge(ORG, mallory, own, victim, reason='duplicate')
    assert refused.value.status == 409 and 'merge card' in str(refused.value)
    with store.engine.connect() as c:
        rows = {r['id']: r for r in c.execute(select(s.records).where(
            s.records.c.org_id == ORG, s.records.c.id.in_([own, victim]))).mappings()}
        assert rows[own]['merged_into'] is None and rows[own]['account_id'] == '40'
        assert rows[victim]['account_id'] is None
        assert store.persons._held(c, ORG, 'email:' + ATTACKER) == own


def test_a_writer_does_not_read_the_victims_private_profile_after_their_merge(store):
    _, _, mallory, val = _writer_merges_own_person_into_victim(store)
    mine = store.profiles.mine(ORG, mallory)
    assert 'PRIVATE-BIO-7731' not in json.dumps(mine) and not mine.get('via_merge')
    assert store.profiles.mine(ORG, val)['fields'] == {'bio': 'PRIVATE-BIO-7731'}    # the victim still does


def test_a_writer_is_not_shown_as_the_victim_in_the_people_directory(store):
    _writer_merges_own_person_into_victim(store)
    out = store.profiles.directory(ORG, [{'subject': '40', 'email': ATTACKER, 'email_verified': True},
                                         {'subject': '21', 'email': VICTIM, 'email_verified': True}])
    assert out['40'] is None
    assert out['21'] == {'bio': 'PRIVATE-BIO-7731'}


def test_the_victim_is_offered_no_merge_card_with_the_writers_account(store):
    victim, own, mallory, val = _writer_merges_own_person_into_victim(store)
    me = store.persons.me(ORG, val)
    assert me['person'] == victim and me['accounts'] == 1 and me['merge'] is None
    assert store.persons.me(ORG, mallory)['merge'] is None
    with store.engine.connect() as c:
        assert store.persons._waiting_for(c, ORG, '40') == []
        assert store.persons.linked_accounts(c, ORG, '21') == ['21']       # a grant to Val reaches Val only
    out = store.persons.group(ORG, [{'subject': '21', 'email': VICTIM, 'email_verified': True}])['21']
    assert out['accounts'] == ['21'] and out['confirmed'] == ['21'] and out['person'] == victim


def test_the_writers_invites_still_go_to_the_writers_own_address(store):
    _, own, _, _ = _writer_merges_own_person_into_victim(store)
    out = store.persons.group(ORG, [{'subject': '40', 'email': ATTACKER, 'email_verified': True}])['40']
    assert out['person'] == own and out['accounts'] == ['40']
    assert out['primary'] == ATTACKER


def test_a_record_an_account_is_linked_to_does_not_merge_away_either(store):
    """The other account binding: an ``account:`` link (a second sign-in that resolved to a record
    with no attached account) is the person's too, and a CRM merge does not carry it."""
    _writer(store, '40')
    mallory_unproven = from_validate('t', {**MALLORY, 'email_verified': False})
    store.ensure_account(ORG, mallory_unproven)                  # a Person attached to account 40
    org_made = store.create(ORG, ADMIN, 'Person', {'Name': 'M.'}, reason='met', idempotency_key='m-org',
                            match=['email:' + ATTACKER])['record_id']
    mallory = from_validate('t', MALLORY)
    assert store.ensure_account(ORG, mallory) == org_made        # the proven address links 40 to it
    with store.engine.connect() as c:
        assert store.persons._held(c, ORG, 'account:40') == org_made
    victim = _victim_record(store)
    with pytest.raises(CRMError):
        store.merge(ORG, mallory, org_made, victim, reason='duplicate')
    with store.engine.connect() as c:
        assert store.persons._held(c, ORG, 'account:40') == org_made
        assert store.persons.resolve(c, ORG, '40')['id'] == org_made


def test_a_duplicate_still_merges_into_a_sign_in_person_and_its_data_moves(store):
    """Data merges continue: a duplicate the org made merges INTO the record an account answers to;
    its identifiers move, the account stays where it is."""
    _writer(store, '40')
    mallory = from_validate('t', MALLORY)
    own = store.ensure_account(ORG, mallory)
    dup = store.create(ORG, ADMIN, 'Person', {'Name': 'Mallory (old)'}, reason='import', idempotency_key='dup',
                       match=['email:mallory@home.test'])['record_id']
    assert store.merge(ORG, mallory, dup, own, reason='duplicate')['merged_into'] == own
    with store.engine.connect() as c:
        assert store.persons._held(c, ORG, 'email:mallory@home.test') == own
        assert store.persons.resolve(c, ORG, '40')['id'] == own
    assert store.get(ORG, ADMIN, dup)['id'] == own


def test_the_persons_own_confirmed_merge_card_still_joins_their_accounts(store):
    """Positive control: two sign-ins of one human, each with a Person of its own, become one Person
    through the merge card both accounts confirm; the account bindings move there."""
    work = from_validate('t', VAL)
    home = from_validate('t', {'user_id': 22, 'email': 'val@home.test', 'email_verified': True,
                               'scopes': ['bot'], 'is_admin': False})
    a = store.ensure_account(ORG, work)
    b = store.ensure_account(ORG, home)
    assert a != b
    store.persons.offer_for_address(ORG, work, 'val@home.test')
    card = store.persons.me(ORG, home)['merge']['id']
    assert store.persons.confirm(ORG, home, card)['status'] == 'merged'
    with store.engine.connect() as c:
        assert store.persons.resolve(c, ORG, '22')['id'] == a
        assert store.persons.resolve(c, ORG, '22', 'val@home.test')['id'] == a
        assert sorted(store.persons.linked_accounts(c, ORG, '21')) == ['21', '22']
    out = store.persons.group(ORG, [{'subject': '22', 'email': 'val@home.test', 'email_verified': True}])['22']
    assert out['person'] == a and out['merged'] is True and sorted(out['confirmed']) == ['21', '22']


# ── car 42: only identity binds an address to an account (A2-c reverse, A2-b with two writers) ──

BOB = {'user_id': 50, 'email': 'bob@acme.test', 'email_verified': True, 'scopes': ['bot'], 'is_admin': False}
MALLORY_TWO = {'user_id': 41, 'email': 'mallory.two@home.test', 'email_verified': True, 'scopes': ['bot'],
               'is_admin': False}


def _proof(store, address):
    with store.engine.connect() as c:
        return c.execute(select(s.match_ids.c.record_id, s.match_ids.c.proof).where(
            s.match_ids.c.org_id == ORG, s.match_ids.c.identifier == 'email:' + address)).first()


def test_a_writer_who_merges_an_org_record_into_their_own_person_does_not_get_its_owners_sign_in(store):
    """A2-c reverse: the writer merges the victim's org record (carrying ``email:<victim>``) INTO the
    writer's own sign-in Person, a data merge the 409 allows. The victim's verified sign-in must not
    land on the writer's Person, be linked there, or be offered a merge card with the writer."""
    victim = _victim_record(store)
    _writer(store, '40')
    mallory = from_validate('t', MALLORY)
    own = store.ensure_account(ORG, mallory)
    assert store.merge(ORG, mallory, victim, own, reason='duplicate')['merged_into'] == own
    val = from_validate('t', VAL)
    landed = store.ensure_account(ORG, val)
    assert landed != own
    me = store.persons.me(ORG, val)
    assert me['person'] == landed and me['accounts'] == 1 and me['merge'] is None
    with store.engine.connect() as c:
        assert store.persons._waiting_for(c, ORG, '40') == [] and store.persons._waiting_for(c, ORG, '21') == []
        assert store.persons.resolve(c, ORG, '21', VICTIM)['id'] == landed
        assert store.persons.linked(c, ORG, store.persons.resolve(c, ORG, '40')) == ['40']
    assert store.persons.group(ORG, [{'subject': '40', 'email': ATTACKER, 'email_verified': True}])['40']['accounts'] == ['40']
    assert _proof(store, VICTIM) == (landed, 'signin:21')        # identity took the address back


def test_a_merged_in_address_claims_nothing_for_its_owner_on_the_writers_person(store):
    """The same move, read through the claim: the victim's sign-in claims a Person of their own, never
    the writer's, and the writer's profile answer never becomes theirs."""
    victim = _victim_record(store)
    _writer(store, '40')
    mallory = from_validate('t', MALLORY)
    own = store.ensure_account(ORG, mallory)
    with store.engine.begin() as c:
        assert store.profiles.auto_claim(c, ORG, mallory) == own
    store.profiles.edit(ORG, mallory, {'bio': 'WRITER-BIO'})
    store.merge(ORG, mallory, victim, own, reason='duplicate')
    val = from_validate('t', VAL)
    store.ensure_account(ORG, val)
    with store.engine.begin() as c:
        claimed = store.profiles.auto_claim(c, ORG, val)
    assert claimed not in (None, own)
    assert store.profiles.mine(ORG, val)['fields'] == {} and not store.profiles.mine(ORG, val).get('via_merge')


def test_two_writers_cannot_move_one_writers_address_onto_someone_elses_record(store):
    """A2-b with two colluding writers: Mallory creates a record carrying Bob's address and merges it
    into Val's org record; Bob's verified sign-in must neither attach to nor claim Val's record."""
    victim = _victim_record(store)
    _writer(store, '40')
    mallory = from_validate('t', MALLORY)
    carrier = store.create(ORG, mallory, 'Person', {'Name': 'B'}, reason='dup', idempotency_key='carrier',
                           match=['email:bob@acme.test'])['record_id']
    store.merge(ORG, mallory, carrier, victim, reason='duplicate')
    bob = from_validate('t', BOB)
    assert store.ensure_account(ORG, bob) != victim
    with store.engine.begin() as c:
        assert store.profiles.auto_claim(c, ORG, bob) != victim
    assert _claim_status(store, bob, victim)['refused'] in (403, 404, 409)
    assert _claimed_by(store, victim) is None
    with store.engine.connect() as c:
        row = c.execute(select(s.records).where(s.records.c.org_id == ORG, s.records.c.id == victim)).mappings().first()
    assert row['account_id'] is None
    # the person the org's record is about still takes it at their first verified sign-in
    val = from_validate('t', VAL)
    assert store.ensure_account(ORG, val) == victim
    with store.engine.begin() as c:
        assert store.profiles.auto_claim(c, ORG, val) == victim


def test_a_second_address_listed_at_creation_does_not_join_a_writer_to_the_persons_record(store):
    """A writer creates Val's record listing Val's address AND one of the writer's own. Val signs in,
    claims it and keeps a private bio. The writer's sign-in with the listed address proved it only
    for the writer's account, which the record does not answer to: no link, no merge card, no read."""
    _writer(store, '40')
    mallory = from_validate('t', MALLORY)
    rid = store.create(ORG, mallory, 'Person', {'Name': 'Val Example'}, reason='met', idempotency_key='two',
                       match=['email:' + VICTIM, 'email:' + MALLORY_TWO['email']])['record_id']
    val = from_validate('t', VAL)
    assert store.ensure_account(ORG, val) == rid
    with store.engine.begin() as c:
        assert store.profiles.auto_claim(c, ORG, val) == rid
    store.profiles.edit(ORG, val, {'bio': 'PRIVATE-BIO-7731'})
    second = from_validate('t', MALLORY_TWO)
    assert store.ensure_account(ORG, second) != rid
    mine = store.profiles.mine(ORG, second)
    assert 'PRIVATE-BIO-7731' not in json.dumps(mine) and not mine.get('via_merge')
    assert store.profiles.directory(ORG, [{'subject': '41'}])['41'] is None
    assert store.persons.me(ORG, val)['merge'] is None and store.persons.me(ORG, val)['accounts'] == 1


def test_an_imported_record_becomes_the_persons_at_their_verified_sign_in(store):
    """Item 3, the legitimate path: the org's record of someone who has not signed in carries their
    address from its creation; the first sign-in identity proves that address for attaches and claims
    it, and the identifier is proven for that account from then on."""
    rid = _victim_record(store)
    val = from_validate('t', VAL)
    assert store.ensure_account(ORG, val) == rid
    with store.engine.begin() as c:
        assert store.profiles.auto_claim(c, ORG, val) == rid
    assert store.persons.me(ORG, val)['person'] == rid


def test_who_set_an_address_is_recorded_and_only_identity_proves_one(store):
    """``crm_match_ids.proof``: an address given at creation is the record's; a CRM merge carries it
    without a proof; identity's sign-in proves it for the account."""
    rid = _victim_record(store)
    assert _proof(store, VICTIM) == (rid, 'record')
    _writer(store, '40')
    mallory = from_validate('t', MALLORY)
    carrier = store.create(ORG, mallory, 'Person', {'Name': 'B'}, reason='dup', idempotency_key='carrier',
                           match=['email:bob@acme.test'])['record_id']
    store.merge(ORG, mallory, carrier, rid, reason='duplicate')
    assert _proof(store, 'bob@acme.test') == (rid, None)
    store.ensure_account(ORG, from_validate('t', VAL))
    assert _proof(store, VICTIM) == (rid, 'signin:21')


def test_an_org_record_merged_into_the_persons_own_sign_in_moves_its_data_to_them(store):
    """The operator's path for a record whose address a merge carried (it binds nobody): the person
    signs in, gets a Person of their own, and the org merges its record INTO that Person."""
    victim = _victim_record(store)
    other = store.create(ORG, ADMIN, 'Person', {'Name': 'Val (old)'}, reason='import', idempotency_key='old')['record_id']
    store.merge(ORG, ADMIN, victim, other, reason='duplicate')      # the address is carried now
    val = from_validate('t', VAL)
    own = store.ensure_account(ORG, val)
    assert own not in (victim, other)
    assert store.merge(ORG, ADMIN, other, own, reason='this is Val')['merged_into'] == own
    assert store.get(ORG, ADMIN, other)['id'] == own
    with store.engine.connect() as c:
        assert store.persons.resolve(c, ORG, '21', VICTIM)['id'] == own


def test_the_proof_of_an_existing_install_is_filled_once_on_the_upgrade_to_9(store):
    """Schema 9's backfill: an identifier on a record a CRM merge carried identifiers into binds
    nobody; one on a record an account answers to is proven for that account; any other is the org's
    record of someone who has not signed in."""
    from crm import migrate
    a = store.create(ORG, ADMIN, 'Person', {'Name': 'A'}, reason='r', idempotency_key='a', match=['email:a@x.test'])['record_id']
    b = store.create(ORG, ADMIN, 'Person', {'Name': 'B'}, reason='r', idempotency_key='b', match=['email:b@x.test'])['record_id']
    d = store.create(ORG, ADMIN, 'Person', {'Name': 'D'}, reason='r', idempotency_key='d', match=['email:d@x.test'])['record_id']
    store.merge(ORG, ADMIN, d, b, reason='duplicate')
    signed = store.ensure_account(ORG, from_validate('t', {**VAL, 'email': 'v@x.test'}))
    with store.engine.begin() as c:
        c.execute(s.match_ids.update().where(s.match_ids.c.org_id == ORG).values(proof=None))   # as before 9
        migrate.match_proof_9(c)
    assert _proof(store, 'a@x.test') == (a, 'record')
    assert _proof(store, 'b@x.test') == (b, None) and _proof(store, 'd@x.test') == (b, None)
    assert _proof(store, 'v@x.test') == (signed, 'signin:21')
