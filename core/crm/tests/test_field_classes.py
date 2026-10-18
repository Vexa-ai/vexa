"""D4 (PRD 0.13.4) and converged model §15: per-field classification and the room's field ceiling.

Offline: SQLite, no identity. A "room" is a shared delegation (``caller.shared``); its members are
the roster of its workspaces (``crm_workspace_rosters``, carried by the workspace snapshot).
"""
import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.bootstrap import DEFAULT_CLASSES, bootstrap_org
from crm.caller import Caller, from_validate
from crm.card_ops import Cards
from crm.field_classes import default_class
from crm.store import Forbidden, SharedRefused, Store, audit_principal

ORG = 'one'
ADMIN = Caller(subject='1', email='admin@acme.test', is_admin=True)


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


ROSTER = [{'subject': '2', 'role': 'owner'}, {'subject': '3', 'role': 'contributor'}]


def company(store, *workspaces, owner=ADMIN, roster=ROSTER, key='co'):
    rid = store.create(ORG, owner, 'Company', {'Name': 'Globex', 'Domain': 'globex.test', 'Stage': 'Negotiation',
                                               'LastContact': '2026-10-01'}, narrative='private notes',
                       reason='fixture', idempotency_key=key, match=[f'domain:{key}.globex.test'])['record_id']
    for w in workspaces:
        store.apply_workspace_snapshot(ORG, w, 'c1', '2026-10-17T10:00:00Z', [], roster)
        store.bind(ORG, ADMIN, w, rid)
    return rid


def room(*workspaces, person_is_admin=False, subject=2):
    ws = list(workspaces or ['team'])
    return from_validate('vxd_room.y.z', {'user_id': subject, 'scopes': ['bot', 'tx'], 'person_is_admin': person_is_admin,
                                          'workspaces': ws, 'writable_workspaces': ws,
                                          'delegation': {'regime': 'human', 'workspaces': ws, 'shared': True,
                                                         'unit': 'room-1', 'on_behalf_of': '3'}})


def restrict(store, subject, kind, fields):
    with store.engine.begin() as c:
        c.execute(s.policies.insert().values(org_id=ORG, subject_id=subject, policy={'active': True, 'objects': {
            kind: {'read': 'none', 'read_fields': fields, 'write': 'none'}}}))


def classify(store, kind, field, cls, workspaces=()):
    with store.engine.begin() as c:
        fc = s.field_classes
        done = c.execute(update(fc).where(fc.c.org_id == ORG, fc.c.object_type == kind, fc.c.field == field)
                         .values({'class': cls, 'workspaces': list(workspaces), 'set_by': 'test'})).rowcount
        if not done:
            c.execute(fc.insert().values(org_id=ORG, object_type=kind, field=field, workspaces=list(workspaces),
                                         set_by='test', set_at='2026-10-17', **{'class': cls}))


def room_rule(store, rule):
    with store.engine.begin() as c:
        policy = c.execute(select(s.orgs.c.default_policy).where(s.orgs.c.org_id == ORG)).scalar_one()
        c.execute(update(s.orgs).where(s.orgs.c.org_id == ORG).values(default_policy={**policy, 'room_fields': rule}))


# ── classification defaults (seeded policy) ──────────────────────────────────────────────────────
def test_defaults_are_seeded_rows_private_for_contact_history_and_identifiers_org_for_basics(store):
    with store.engine.connect() as c:
        rows = {(r['object_type'], r['field']): r['class'] for r in c.execute(select(s.field_classes)).mappings()}
    assert rows[('Company', 'LastContact')] == 'private' and rows[('Person', 'LastContact')] == 'private'
    assert rows[('Person', '$match_ids')] == 'private' and rows[('Company', '$match_ids')] == 'private'
    assert rows[('Company', 'Name')] == rows[('Company', 'Domain')] == 'org'
    assert rows[('Company', '$narrative')] == 'org'
    assert len(rows) == len(DEFAULT_CLASSES)


def test_bootstrap_never_overwrites_a_class_an_administrator_set(store):
    classify(store, 'Company', 'Name', 'ws', ['team'])
    assert 'field_classes' not in bootstrap_org(store.engine, ORG, 'Acme')
    with store.engine.connect() as c:
        row = c.execute(select(s.field_classes).where(s.field_classes.c.field == 'Name',
                                                      s.field_classes.c.object_type == 'Company')).mappings().one()
    assert row['class'] == 'ws' and row['workspaces'] == ['team']


@pytest.mark.parametrize('name, prop, want', [
    ('lastContactAt', {}, 'private'), ('Last Email Subject', {}, 'private'), ('InteractionsTimeline', {}, 'private'),
    ('Summary', {'x-source': 'email'}, 'private'), ('Industry', {}, 'org'), ('Name', {}, 'org'),
    ('LastContact', {'x-class': 'org'}, 'org'), ('Amount', {'x-class': 'private'}, 'private'),
])
def test_an_unclassified_field_takes_the_same_rule_as_the_seed(name, prop, want):
    assert default_class(name, prop)[0] == want


def test_a_property_can_declare_a_workspace_share_in_its_definition():
    assert default_class('Stage', {'x-class': {'ws': ['team']}}) == ('ws', ('team',))


# ── private: the owner only ──────────────────────────────────────────────────────────────────────
def test_a_private_field_is_absent_for_a_non_owner_and_present_for_owner_and_admin(store):
    owner = Caller(subject='5', workspaces=('team',))
    rid = company(store, 'team', owner=owner, roster=[{'subject': '5', 'role': 'owner'},
                                                      {'subject': '6', 'role': 'viewer'}])
    member = Caller(subject='6', workspaces=('team',))
    seen = store.get(ORG, member, rid)
    assert 'LastContact' not in seen['fields'] and seen['fields']['Stage'] == 'Negotiation'
    assert 'match_ids' not in seen                                          # absent, never blanked
    assert store.get(ORG, owner, rid)['fields']['LastContact'] == '2026-10-01'
    assert store.get(ORG, ADMIN, rid)['fields']['LastContact'] == '2026-10-01'
    with pytest.raises(Forbidden, match='Filter field unavailable'):
        store.search(ORG, member, 'Company', {'LastContact': '2026-10-01'})


# ── the room ceiling: the members' intersection (interim rule) ───────────────────────────────────
def test_a_room_is_never_admin_and_reads_only_what_every_member_reads(store):
    rid = company(store, 'team')
    restrict(store, '3', 'Company', ['Name', 'Domain', 'LastContact'])
    turn = room(person_is_admin=True)
    assert turn.is_admin is False
    rec = store.get(ORG, turn, rid)
    assert set(rec['fields']) == {'Name', 'Domain'}           # Stage: member 3 may not; LastContact: private
    assert rec['narrative'] is None and 'match_ids' not in rec and 'account_id' not in rec
    listed = store.search(ORG, turn, 'Company')['records']
    assert [set(r['fields']) for r in listed] == [{'Name', 'Domain'}]
    with pytest.raises(Forbidden, match='Filter field unavailable'):
        store.search(ORG, turn, 'Company', {'Stage': 'Negotiation'})
    assert set(store.describe(ORG, turn, 'Company')['objects'][0]['fields']) == {'Name', 'Domain'}


def test_a_private_field_never_reaches_a_room_even_when_every_member_reads_everything(store):
    rid = company(store, 'team')
    rec = store.get(ORG, room(), rid)
    assert rec['fields']['Stage'] == 'Negotiation' and 'LastContact' not in rec['fields']
    assert rec['narrative'] is None


def test_a_room_whose_member_has_no_crm_membership_reads_no_fields(store):
    rid = company(store, 'team')
    with store.engine.begin() as c:
        c.execute(s.policies.insert().values(org_id=ORG, subject_id='3', policy={'active': False}))
    assert store.get(ORG, room(), rid)['fields'] == {}


def test_a_room_with_no_roster_fails_closed(store):
    rid = company(store)
    with store.engine.begin() as c:
        c.execute(s.workspace_bindings.insert().values(org_id=ORG, workspace='team', record_id=rid,
                                                       created_by='1', created_at='2026-10-17'))
    assert store.get(ORG, room(), rid)['fields'] == {}


def test_bound_to_is_the_ceiling_for_a_room_and_for_an_admins_worker(store):
    rid = company(store, 'team', 'board-private')
    assert store.get(ORG, room(person_is_admin=True), rid)['bound_to'] == ['team']
    worker = Caller(subject='1', is_admin=True, delegated=True, regime='human', workspaces=('team',))
    assert store.get(ORG, worker, rid)['bound_to'] == ['team']
    assert store.get(ORG, ADMIN, rid)['bound_to'] == ['board-private', 'team']


def test_an_admin_speaker_still_gets_the_shared_conversation_refusal_not_admin_rights(store):
    unbound = company(store, key='unbound')
    company(store, 'team', key='bound')
    with pytest.raises(SharedRefused):
        store.get(ORG, room(person_is_admin=True), unbound)


# ── explicit workspace-shareable classification ──────────────────────────────────────────────────
def test_a_field_shared_with_the_workspace_reaches_its_room_and_no_other(store):
    rid = company(store, 'team', 'sales')
    restrict(store, '3', 'Company', ['Name'])
    classify(store, 'Company', 'Stage', 'ws', ['team'])
    assert set(store.get(ORG, room('team'), rid)['fields']) == {'Name', 'Stage'}
    assert set(store.get(ORG, room('sales'), rid)['fields']) == {'Name'}


def test_the_narrative_reaches_a_room_only_when_shared_with_it(store):
    rid = company(store, 'team')
    assert store.get(ORG, room(), rid)['narrative'] is None
    classify(store, 'Company', '$narrative', 'ws', ['team'])
    assert store.get(ORG, room(), rid)['narrative'] == 'private notes'


def test_the_classified_rule_reads_only_shared_fields(store):
    rid = company(store, 'team')
    room_rule(store, 'classified')
    assert store.get(ORG, room(), rid)['fields'] == {}
    classify(store, 'Company', 'Name', 'ws', ['team'])
    assert store.get(ORG, room(), rid)['fields'] == {'Name': 'Globex'}


def test_an_unknown_room_rule_fails_closed_to_classified(store):
    rid = company(store, 'team')
    room_rule(store, 'everything')
    assert store.get(ORG, room(), rid)['fields'] == {}


# ── card faces ───────────────────────────────────────────────────────────────────────────────────
def test_a_room_card_face_locks_a_field_outside_the_ceiling(store):
    cards = Cards(store)
    ana = Caller(subject='2', workspaces=('team',), writable=('team',))
    store.apply_workspace_snapshot(ORG, 'team', 'c1', '2026-10-17T10:00:00Z', [], ROSTER)
    card = cards.create(ORG, ana, title='Renewal', workspace='team', fields={'Summary': 'margin is 12%'},
                        face=[{'type': 'text', 'field': 'Summary', 'label': 'Summary'}])['card']
    with store.engine.begin() as c:
        c.execute(s.policies.insert().values(org_id=ORG, subject_id='3', policy={'active': True, 'objects': {
            'Card': {'read': 'none', 'read_fields': ['Title', 'State', 'Face'], 'write': 'none'}}}))
    face = cards.read(ORG, room(), card['id'])['face']
    assert {'type': 'locked', 'label': 'Summary'} in face
    assert 'margin is 12%' not in str(face)


# ── audit (H9) ───────────────────────────────────────────────────────────────────────────────────
def test_the_audit_names_the_room_principal_the_person_acted_for_and_the_workspace():
    assert audit_principal(room('team')) == {'principal': 'ws:team', 'on_behalf_of': '3', 'workspace': 'team'}
    assert audit_principal(Caller(subject='7')) == {'principal': '7', 'on_behalf_of': '', 'workspace': ''}
    worker = Caller(subject='7', delegated=True, regime='human', workspaces=('sales',))
    assert audit_principal(worker) == {'principal': '7', 'on_behalf_of': '7', 'workspace': 'sales'}
    ws_principal = Caller(subject='ws:team', delegated=True, shared=True, workspaces=('team',))
    assert audit_principal(ws_principal)['principal'] == 'ws:team'
