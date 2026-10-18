"""ADR-0047: the organisation's registry beside workspaces — accounts, merges, backlinks, bindings,
derived grants and the leak rule, against the store the HTTP and MCP surfaces share."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.bootstrap import bootstrap_org
from crm.caller import Caller
from crm.store import Store, NotFound, Forbidden, Conflict, Refused, SharedRefused

ORG = 'acme'
SCHEMA = json.loads((Path(__file__).parents[1] / 'contracts/crm.v1/crm.schema.json').read_text())


def conforms(shape, data):
    errors = list(Draft202012Validator({'$ref': '#/$defs/' + shape, '$defs': SCHEMA['$defs']}).iter_errors(data))
    assert not errors, errors[0].message


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


ADMIN = Caller(subject='1', email='admin@acme.test', is_admin=True)


def person(uid, *workspaces, email='', **kw):
    return Caller(subject=uid, email=email or f'u{uid}@acme.test', workspaces=tuple(workspaces), **kw)


def company(store, name='Globex'):
    return store.create(ORG, ADMIN, 'Company', {'Name': name}, reason='fixture',
                        idempotency_key='co-' + name, match=['domain:' + name.lower() + '.test'])['record_id']


def snapshot(store, workspace, refs, members, at='2026-10-13T10:00:00Z', commit='c1'):
    return store.apply_workspace_snapshot(ORG, workspace, commit, at,
                                          [{'page': p, 'record': r} for p, r in refs], members)


# ── B: organisation, user ↔ person ─────────────────────────────────────────────────────────
def test_sign_in_creates_a_person_with_the_account_attached_once(store):
    first = store.ensure_account(ORG, person('7'))
    assert first and store.ensure_account(ORG, person('7')) == first
    read = store.get(ORG, ADMIN, first)
    assert read['object_type'] == 'Person' and read['account_id'] == '7'
    assert read['match_ids'] == ['email:u7@acme.test']
    # the person reads their own entity through the organisation's default policy (own scope)
    assert store.get(ORG, person('7'), first)['fields']['Email'] == 'u7@acme.test'


def test_sign_in_attaches_an_existing_external_person_by_verified_email(store):
    external = store.create(ORG, ADMIN, 'Person', {'Name': 'Jane Roe'}, reason='met at a call',
                            idempotency_key='jane', match=['email:Jane@Example.test'])['record_id']
    assert store.get(ORG, ADMIN, external)['account_id'] is None
    assert store.ensure_account(ORG, person('9', email='jane@example.test')) == external
    assert store.get(ORG, ADMIN, external)['account_id'] == '9'


def test_one_identifier_is_one_record(store):
    company(store)
    with pytest.raises(Conflict):
        store.create(ORG, ADMIN, 'Company', {'Name': 'Globex 2'}, reason='dup', idempotency_key='x',
                     match=['domain:GLOBEX.test'])


def test_merge_answers_the_survivor_for_the_old_id_and_moves_identifiers(store):
    keep = company(store, 'Initech')
    dup = store.create(ORG, ADMIN, 'Company', {'Name': 'Initech Inc'}, reason='dup',
                       idempotency_key='dup', match=['domain:initech.example'])['record_id']
    snapshot(store, 'sales', [('accounts/initech.md', dup)], [{'subject': '2', 'role': 'viewer'}])
    assert store.merge(ORG, ADMIN, dup, keep, reason='same company')['merged_into'] == keep
    read = store.get(ORG, ADMIN, dup)
    assert read['id'] == keep and 'domain:initech.example' in read['match_ids']
    assert read['mentioned_in'] == [{'workspace': 'sales', 'page': 'accounts/initech.md'}]
    with pytest.raises(Refused):
        store.merge(ORG, Caller(subject='1', is_admin=True, delegated=True, regime='human'), keep, dup, reason='x')


# ── D: references and the backlink visibility rule ─────────────────────────────────────────
def test_backlinks_only_from_workspaces_the_caller_is_a_member_of(store):
    rid = company(store)
    snapshot(store, 'sales', [('accounts/globex.md', rid)], [{'subject': '2', 'role': 'contributor'}])
    snapshot(store, 'board', [('minutes/q3.md', rid)], [{'subject': '3', 'role': 'viewer'}])
    store.bind(ORG, ADMIN, 'sales', rid)
    store.bind(ORG, ADMIN, 'board', rid)
    sales = store.get(ORG, person('2', 'sales'), rid)
    assert sales['mentioned_in'] == [{'workspace': 'sales', 'page': 'accounts/globex.md'}]
    assert 'board' not in json.dumps(sales)          # omitted entirely — no count, no binding name
    everything = store.get(ORG, ADMIN, rid)['mentioned_in']
    assert {m['workspace'] for m in everything} == {'sales', 'board'}
    conforms('Record', sales)


def test_a_snapshot_replaces_the_set_and_an_older_commit_never_wins(store):
    rid = company(store)
    snapshot(store, 'sales', [('a.md', rid), ('b.md', rid)], [], at='2026-10-13T10:00:00Z')
    snapshot(store, 'sales', [('b.md', rid)], [], at='2026-10-13T11:00:00Z', commit='c2')
    assert snapshot(store, 'sales', [('a.md', rid)], [], at='2026-10-13T09:00:00Z', commit='c0')['status'] == 'stale'
    assert store.get(ORG, ADMIN, rid)['mentioned_in'] == [{'workspace': 'sales', 'page': 'b.md'}]


def test_a_link_never_grants_access(store):
    rid = company(store)
    snapshot(store, 'sales', [('accounts/globex.md', rid)], [{'subject': '2', 'role': 'owner'}])
    with pytest.raises(NotFound):
        store.get(ORG, person('2', 'sales'), rid)


# ── E: bindings and derived grants ─────────────────────────────────────────────────────────
def test_binding_derives_read_for_readers_and_write_for_contributors(store):
    rid = company(store)
    snapshot(store, 'sales', [], [{'subject': '2', 'role': 'contributor'}, {'subject': '3', 'role': 'viewer'}])
    store.bind(ORG, ADMIN, 'sales', rid)
    assert store.get(ORG, person('3', 'sales'), rid)['fields']['Name'] == 'Globex'
    with pytest.raises(NotFound):
        store.change(ORG, person('3', 'sales'), rid, 1, {'Name': 'Nope'}, reason='x')
    assert store.change(ORG, person('2', 'sales'), rid, 1, {'Stage': 'Customer'}, reason='call')['status'] == 'saved'
    assert [r['id'] for r in store.search(ORG, person('3', 'sales'), 'Company')['records']] == [rid]


def test_leaving_the_workspace_revokes_the_derived_grant(store):
    rid = company(store)
    snapshot(store, 'sales', [], [{'subject': '2', 'role': 'contributor'}])
    store.bind(ORG, ADMIN, 'sales', rid)
    assert store.get(ORG, person('2', 'sales'), rid)
    # identity no longer answers 'sales' among the person's memberships: the grant is gone at once
    with pytest.raises(NotFound):
        store.get(ORG, person('2'), rid)
    assert store.search(ORG, person('2'), 'Company')['records'] == []
    # and the roster dropping them removes the write role too
    snapshot(store, 'sales', [], [], at='2026-10-13T12:00:00Z')
    with pytest.raises(NotFound):
        store.change(ORG, person('2', 'sales'), rid, 1, {'Stage': 'Lost'}, reason='x')


def test_a_binding_never_widens_a_field_restriction_the_policy_sets(store):
    rid = store.create(ORG, ADMIN, 'Deal', {'Name': 'Renewal', 'Amount': 50000}, reason='fixture',
                       idempotency_key='deal')['record_id']
    with store.engine.begin() as c:
        c.execute(s.policies.insert().values(org_id=ORG, subject_id='4', policy={'active': True, 'objects': {
            'Deal': {'read': 'none', 'read_fields': ['Name'], 'write': 'none'}}}))
    snapshot(store, 'sales', [], [{'subject': '4', 'role': 'owner'}])
    store.bind(ORG, ADMIN, 'sales', rid)
    read = store.get(ORG, person('4', 'sales'), rid)
    assert read['fields'] == {'Name': 'Renewal'} and read['narrative'] is None


def test_only_an_administrator_binds(store):
    rid = company(store)
    with pytest.raises(Forbidden):
        store.bind(ORG, person('2', 'sales'), 'sales', rid)
    with pytest.raises(Refused):
        store.bind(ORG, Caller(subject='1', is_admin=True, delegated=True, regime='autonomous'), 'sales', rid)


# ── F: the leak rule ───────────────────────────────────────────────────────────────────────
def test_a_shared_turn_is_refused_a_record_only_the_speaker_can_read(store):
    mine = store.ensure_account(ORG, person('2'))                       # A's own person entity
    shared = company(store)
    snapshot(store, 'team', [], [{'subject': '2', 'role': 'contributor'}, {'subject': '3', 'role': 'viewer'}])
    store.bind(ORG, ADMIN, 'team', shared)
    turn = person('2', 'team', delegated=True, regime='human', shared=True)
    with pytest.raises(SharedRefused) as refused:
        store.get(ORG, turn, mine)
    assert refused.value.body['reason'] == 'shared_conversation'
    conforms('Refusal', refused.value.body)
    assert store.get(ORG, turn, shared)['id'] == shared                 # in the intersection
    assert [r['id'] for r in store.search(ORG, turn, 'Person')['records']] == []
    # the same record in A's private chat is answered
    assert store.get(ORG, person('2', 'team', delegated=True, regime='human'), mine)['id'] == mine


def test_a_shared_turn_never_learns_of_a_record_the_speaker_cannot_read(store):
    hidden = company(store)
    turn = person('2', delegated=True, regime='human', shared=True)
    with pytest.raises(NotFound) as e:
        store.get(ORG, turn, hidden)
    assert not isinstance(e.value, SharedRefused)


# ── agents propose; people accept ─────────────────────────────────────────────────────────
def test_an_agent_proposes_and_only_a_person_accepts(store):
    rid = company(store)
    agent = Caller(subject='1', is_admin=True, delegated=True, regime='human')
    out = store.change(ORG, agent, rid, 1, {'Stage': 'Customer'}, reason='said so in the call',
                       evidence=[{'meeting': 'm-1'}])
    conforms('ChangeResult', out)
    assert out['status'] == 'proposed'
    with pytest.raises(Refused) as refused:
        store.review(ORG, agent, out['proposal_id'], True)
    assert refused.value.body['reason'] == 'person_confirmation_required'
    pending = store.get(ORG, ADMIN, rid)['proposals']
    assert pending[0]['author_kind'] == 'agent'
    assert store.review(ORG, ADMIN, out['proposal_id'], True)['status'] == 'saved'


def test_an_unwatched_worker_reads_and_proposes_but_never_writes(store):
    rid = company(store)
    worker = Caller(subject='1', is_admin=True, delegated=True, regime='autonomous')
    assert store.get(ORG, worker, rid)
    assert store.change(ORG, worker, rid, 1, {'Stage': 'x'}, reason='r')['status'] == 'proposed'
    with pytest.raises(Refused):
        store.create(ORG, worker, 'Company', {'Name': 'New'}, reason='r', idempotency_key='n')


def test_policy_may_let_an_agent_write_named_fields_directly(store):
    rid = company(store)
    with store.engine.begin() as c:
        c.execute(s.policies.insert().values(org_id=ORG, subject_id='5', policy={'active': True,
            'agent_direct_fields': ['LastContact'], 'objects': {'Company': {'read': 'all', 'read_fields': ['*'],
                                                                          'write': 'all', 'write_fields': ['*']}}}))
    agent = Caller(subject='5', delegated=True, regime='human')
    assert store.change(ORG, agent, rid, 1, {'LastContact': '2026-10-13'}, reason='call')['status'] == 'saved'
    assert store.change(ORG, agent, rid, 2, {'Stage': 'Customer'}, reason='call')['status'] == 'proposed'


def test_every_operation_can_be_audited_with_the_session_trace(store):
    agent = Caller(subject='1', is_admin=True, delegated=True, regime='human', session='unit-1', trace='t-1')
    store.audit(ORG, agent, 'read', 'r-1', 'ok')
    with store.engine.connect() as c:
        row = c.execute(s.audit.select()).mappings().one()
    assert (row['caller'], row['session'], row['trace'], row['op']) == ('agent', 'unit-1', 't-1', 'read')


def test_identitys_live_writable_answer_decides_a_derived_write(store):
    rid = company(store)
    snapshot(store, 'sales', [], [{'subject': '2', 'role': 'contributor'}])
    store.bind(ORG, ADMIN, 'sales', rid)
    # the roster still says contributor, identity no longer lists sales as writable: read only
    reader = Caller(subject='2', email='u2@acme.test', workspaces=('sales',), writable=())
    assert store.get(ORG, reader, rid)['id'] == rid
    with pytest.raises(NotFound):
        store.change(ORG, reader, rid, 1, {'Stage': 'x'}, reason='r')
    writer = Caller(subject='2', email='u2@acme.test', workspaces=('sales',), writable=('sales',))
    assert store.change(ORG, writer, rid, 1, {'Stage': 'Customer'}, reason='r')['status'] == 'saved'
