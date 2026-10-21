"""D35 (PRD 0.13.4 S3): one Person, many addresses. No persons table: the human is the CRM's Person
record, an account resolves to it by its verified address, a grant to the Person reaches every linked
account, and two accounts become one person only on that person's own confirmation.

All data is synthetic (``*.test`` addresses, numeric account ids)."""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.api import Identity, create_app
from crm.bootstrap import bootstrap_org
from crm.caller import Caller
from crm.persons import CONFIRMED, DECLINED, OFFERED, PersonError, PersonNotFound, PersonRefused, mask
from crm.store import Store

ORG = 'acme'
ADMIN = Caller(subject='1', email='admin@acme.test', is_admin=True)
WORK, HOME = 'val@work.test', 'val.home@mail.test'


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


def acct(uid, email, *workspaces, **kw):
    """A signed-in account whose address a sign-in proved (identity.v1 ``email_verified``)."""
    return Caller(subject=uid, email=email, email_verified=True, workspaces=tuple(workspaces), **kw)


A = acct('21', WORK)            # her work login
B = acct('22', HOME)            # her personal login
STRANGER = acct('30', 'someone@else.test')


def founder_record(store):
    """The founder's CRM record of her, holding both addresses."""
    return store.create(ORG, ADMIN, 'Person', {'Name': 'Val Example'}, reason='met at the tinkerers',
                        idempotency_key='val', match=['email:' + WORK, 'email:' + HOME])['record_id']


def card_of(store, person_id):
    p = s.change_proposals
    with store.engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(p).where(p.c.org_id == ORG, p.c.record_id == person_id)).mappings()]
    return [r for r in rows if (r['patch'] or {}).get('kind') == 'person_merge']


# ── resolution: two accounts, one Person ─────────────────────────────────────────────────────
def test_two_accounts_resolve_to_one_person_by_verified_address(store):
    val = founder_record(store)
    assert store.ensure_account(ORG, A) == val            # attached: the first account
    assert store.ensure_account(ORG, B) == val            # linked, never a second record
    grouped = store.persons.group(ORG, [{'subject': '21', 'email': WORK}, {'subject': '22', 'email': HOME}])
    assert grouped['21']['person'] == grouped['22']['person'] == val
    assert sorted(grouped['21']['accounts']) == ['21', '22'] == sorted(grouped['22']['accounts'])
    assert grouped['21']['merged'] is False
    # there is no persons table: the link is a match identifier on the Person record
    assert 'crm_persons' not in s.metadata.tables
    assert store.get(ORG, ADMIN, val)['match_ids'] == sorted(['account:22', 'email:' + HOME, 'email:' + WORK])


def test_the_picker_groups_by_address_even_before_the_second_account_signed_in(store):
    """A producer that vouches an account's address was proven (``email_verified``) groups it with no
    CRM sign-in; an address nobody vouches for groups nothing (``test_person_reads_proven``)."""
    val = founder_record(store)
    store.ensure_account(ORG, A)
    grouped = store.persons.group(ORG, [{'subject': '21', 'email': WORK, 'email_verified': True},
                                        {'subject': '22', 'email': HOME, 'email_verified': True}])
    assert grouped['22']['person'] == val and sorted(grouped['22']['accounts']) == ['21', '22']


def test_an_account_with_no_person_is_a_person_of_one(store):
    grouped = store.persons.group(ORG, [{'subject': '40', 'email': 'new@nobody.test'}])
    assert grouped['40'] == {'person': None, 'accounts': ['40'], 'confirmed': ['40'], 'primary': 'new@nobody.test', 'merged': False}


def test_crm_change_cannot_write_an_account_link(store):
    with pytest.raises(Exception):
        store.create(ORG, ADMIN, 'Person', {'Name': 'X'}, reason='r', idempotency_key='x', match=['account:22'])


# ── grants reach every linked account ────────────────────────────────────────────────────────
def test_a_wrong_address_gets_nothing_before_the_person_confirms(store):
    """Coordinator ruling 2026-10-17: a linked login receives the Person's grants only after the person
    confirmed the merge card from each login's own session. Before that, nothing."""
    val = founder_record(store)
    store.ensure_account(ORG, A)
    store.ensure_account(ORG, B)                      # linked by the record's identifiers, unconfirmed
    from crm.card_ops import Cards
    cards = Cards(store)
    card = cards.create(ORG, ADMIN, 'Send the deck')
    card_id = card['card'].get('id') or card['card'].get('record_id')
    members = {m['subject'] for m in cards.members(ORG, ADMIN, card_id, '21', True)['members']}
    assert '21' in members and '22' not in members
    grouped = store.persons.group(ORG, [{'subject': '21', 'email': WORK}])['21']
    assert grouped['confirmed'] == ['21'] and sorted(grouped['accounts']) == ['21', '22']   # a hint only
    # half a confirmation is still nothing
    merge = store.persons.me(ORG, A)['merge']
    store.persons.confirm(ORG, A, merge['id'])
    assert store.persons.group(ORG, [{'subject': '21', 'email': WORK}])['21']['confirmed'] == ['21']
    # both logins confirmed: now a grant to the person reaches both
    store.persons.confirm(ORG, B, merge['id'])
    assert sorted(store.persons.group(ORG, [{'subject': '21', 'email': WORK}])['21']['confirmed']) == ['21', '22']
    card2 = cards.create(ORG, ADMIN, 'Second card')
    card2_id = card2['card'].get('id') or card2['card'].get('record_id')
    members = {m['subject'] for m in cards.members(ORG, ADMIN, card2_id, '21', True)['members']}
    assert {'21', '22'} <= members
    removed = {m['subject'] for m in cards.members(ORG, ADMIN, card2_id, '22', False)['members']}
    assert not removed & {'21', '22'}
    assert val


# ── the merge card: only the person's own confirmation ───────────────────────────────────────
def test_a_merge_card_is_offered_and_nothing_merges_without_every_account_confirming(store):
    val = founder_record(store)
    store.ensure_account(ORG, A)
    store.ensure_account(ORG, B)
    me = store.persons.me(ORG, A)
    assert me['person'] == val and me['accounts'] == 2
    card = me['merge']
    assert card and card['status'] == OFFERED and card['waiting_on'] == 2
    # her own address in full, the other one masked
    shown = {a['address'] for a in card['addresses']}
    assert WORK in shown and mask(HOME) in shown and HOME not in shown
    work = next(a['option'] for a in card['addresses'] if a['yours'])
    first = store.persons.confirm(ORG, A, card['id'], primary=work)
    assert first['status'] == 'confirmed' and first['merge']['waiting_on'] == 1
    assert store.persons.group(ORG, [{'subject': '21', 'email': WORK}])['21']['merged'] is False
    assert card_of(store, val)[0]['status'] == OFFERED
    done = store.persons.confirm(ORG, B, card['id'])
    assert done['status'] == 'merged'
    assert card_of(store, val)[0]['status'] == CONFIRMED
    grouped = store.persons.group(ORG, [{'subject': '22', 'email': HOME}])['22']
    assert grouped['merged'] is True
    assert grouped['primary'] == WORK            # the person chose it; invites and nudges go there
    assert store.persons.me(ORG, B)['merge'] is None


def test_no_automatic_merge_nobody_else_and_no_agent_confirms(store):
    val = founder_record(store)
    store.ensure_account(ORG, A)
    store.ensure_account(ORG, B)
    card = store.persons.me(ORG, A)['merge']
    agent = acct('21', WORK, delegated=True, regime='human')
    with pytest.raises(PersonRefused):
        store.persons.confirm(ORG, agent, card['id'])
    with pytest.raises(PersonRefused):
        store.persons.me(ORG, agent)
    with pytest.raises(PersonNotFound):
        store.persons.confirm(ORG, STRANGER, card['id'])
    with pytest.raises(PersonNotFound):
        store.persons.confirm(ORG, ADMIN, card['id'])       # not even an administrator
    # crm_review cannot accept it either: it is not a pending record change
    with pytest.raises(Exception):
        store.review(ORG, ADMIN, card['id'], True)
    assert card_of(store, val)[0]['status'] == OFFERED
    assert 'proposals' not in store.get(ORG, ADMIN, val) or not any(
        p['id'] == card['id'] for p in store.get(ORG, ADMIN, val)['proposals'])


def test_declining_splits_the_accounts_so_grants_stop_following(store):
    val = founder_record(store)
    store.ensure_account(ORG, A)
    store.ensure_account(ORG, B)
    card = store.persons.me(ORG, B)['merge']
    assert store.persons.decline(ORG, B, card['id']) == {'status': 'declined', 'split': 1}
    assert card_of(store, val)[0]['status'] == DECLINED
    grouped = store.persons.group(ORG, [{'subject': '21', 'email': WORK}, {'subject': '22', 'email': HOME}])
    assert grouped['21']['person'] == val and grouped['21']['accounts'] == ['21']
    assert grouped['22']['person'] != val and grouped['22']['accounts'] == ['22']
    # a fresh sign-in by B resolves to its own Person now, and no new card appears
    assert store.ensure_account(ORG, B) == grouped['22']['person']
    assert store.persons.me(ORG, A)['merge'] is None


def test_a_person_offers_their_other_address_and_it_completes_only_from_that_account(store):
    a = store.ensure_account(ORG, A)                    # two sign-ups, two Persons
    b = store.ensure_account(ORG, B)
    assert a != b
    unknown = store.persons.offer_for_address(ORG, A, 'nobody@nowhere.test')
    offered = store.persons.offer_for_address(ORG, A, HOME)
    assert unknown == offered                           # the answer never says who has an account
    assert store.persons.group(ORG, [{'subject': '22', 'email': HOME}])['22']['person'] == b
    card = store.persons.me(ORG, B)['merge']
    assert card and card['confirmed_by_you'] is False
    assert store.persons.confirm(ORG, B, card['id'])['status'] == 'merged'
    grouped = store.persons.group(ORG, [{'subject': '22', 'email': HOME}])['22']
    assert grouped['person'] == a and sorted(grouped['accounts']) == ['21', '22']
    assert store.get(ORG, ADMIN, b)['id'] == a          # the old id answers the survivor


def test_a_decided_card_cannot_be_decided_again(store):
    founder_record(store)
    store.ensure_account(ORG, A)
    store.ensure_account(ORG, B)
    card = store.persons.me(ORG, A)['merge']
    store.persons.decline(ORG, A, card['id'])
    with pytest.raises(PersonError):
        store.persons.confirm(ORG, A, card['id'])


# ── field privacy: match identifiers stay private for other viewers ──────────────────────────
def test_match_ids_and_account_links_are_absent_for_other_viewers(store):
    val = founder_record(store)
    store.ensure_account(ORG, A)
    store.ensure_account(ORG, B)
    store.bind(ORG, ADMIN, 'sales', val)
    store.apply_workspace_snapshot(ORG, 'sales', 'c1', '2026-10-17T10:00:00Z', [],
                                   [{'subject': '1', 'role': 'owner'}, {'subject': '50', 'role': 'contributor'}])
    colleague = acct('50', 'colleague@acme.test', 'sales')     # reads the record through the binding
    read = store.get(ORG, colleague, val)
    assert read['fields']['Name'] == 'Val Example'
    assert 'match_ids' not in read and 'account_id' not in read
    # a room reading the bound record: no identifiers either
    room = acct('50', 'colleague@acme.test', 'sales', delegated=True, shared=True, regime='human')
    assert 'match_ids' not in store.get(ORG, room, val)
    # the administrator (and the record's owner) still see them
    assert 'account:22' in store.get(ORG, ADMIN, val)['match_ids']
    # and the person's own view masks every address but the one they are signed in with
    me = store.persons.me(ORG, B)
    assert HOME in me['addresses'] and WORK not in me['addresses'] and mask(WORK) in me['addresses']


# ── the front door: person routes and the internal route ─────────────────────────────────────
def _client(store, users):
    def resolve(request):
        token = json.loads(request.content)['token']
        if token not in users:
            return httpx.Response(401)
        return httpx.Response(200, json=users[token])
    identity = Identity('http://identity', 'internal-test-only', httpx.MockTransport(resolve))
    return TestClient(create_app(store, identity, ORG, internal_secret='s3-internal'))


def test_the_front_door_person_routes_and_internal_grouping(store):
    founder_record(store)
    client = _client(store, {'a': {'user_id': '21', 'email': WORK, 'email_verified': True},
                             'b': {'user_id': '22', 'email': HOME, 'email_verified': True},
                             'vxd_w': {'user_id': '21', 'email': WORK, 'email_verified': True, 'delegation': {'regime': 'human', 'workspaces': []}}})
    me_a = client.post('/person/me', json={}, headers={'X-API-Key': 'a'})
    assert me_a.status_code == 200, me_a.text
    me_b = client.post('/person/me', json={}, headers={'X-API-Key': 'b'}).json()
    assert me_b['accounts'] == 2 and me_b['merge']
    card = me_b['merge']['id']
    assert client.post('/person/merge', json={'action': 'confirm', 'card': card},
                       headers={'Authorization': 'Bearer vxd_w'}).status_code == 403
    assert client.post('/person/merge', json={'action': 'confirm', 'card': card},
                       headers={'X-API-Key': 'a'}).json()['status'] == 'confirmed'
    assert client.post('/person/merge', json={'action': 'confirm', 'card': card},
                       headers={'X-API-Key': 'b'}).json()['status'] == 'merged'
    body = {'accounts': [{'subject': '21', 'email': WORK}, {'subject': '22', 'email': HOME}]}
    assert client.post('/internal/persons', json=body).status_code == 403
    out = client.post('/internal/persons', json=body, headers={'X-Internal-Secret': 's3-internal'}).json()['persons']
    assert out['21']['person'] == out['22']['person'] and out['21']['merged'] is True
    spec = client.get('/openapi.json').json()['paths']
    assert not {'/person/me', '/person/merge', '/internal/persons'} & set(spec)   # never an agent tool


def test_the_confirming_login_may_choose_its_own_address_and_only_a_card_option(store):
    store.ensure_account(ORG, A)
    store.ensure_account(ORG, B)
    store.persons.offer_for_address(ORG, A, HOME)
    card = store.persons.me(ORG, B)['merge']
    mine = [o for o in card['addresses'] if o['yours']]
    assert [o['address'] for o in mine] == [HOME]          # B's own address, in full, before confirming
    assert mask(WORK) in {o['address'] for o in card['addresses']}
    with pytest.raises(PersonError):
        store.persons.confirm(ORG, B, card['id'], primary='not-an-option')
    assert store.persons.confirm(ORG, B, card['id'], primary=mine[0]['option'])['status'] == 'merged'
    assert store.persons.group(ORG, [{'subject': '21', 'email': WORK}])['21']['primary'] == HOME
