"""Cards on the CRM (desktop ADR phase D2): a card is one shared registry record of a card type.

A person makes a card directly; an agent only proposes one (or a change to one), and a person
accepts it. A card's stage follows its type's lifecycle from `_global` policy, and an agent never
moves it. Sharing is membership — the same card, never a copy — and membership never grants what the
card is about. A workspace's board is the cards it references. Faces are composed from the blocks.v1
face profile, sanitised on the server and resolved per viewer, so no viewer sees a field through a
face that they could not read on the record.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.bootstrap import bootstrap_org
from crm.caller import Caller
from crm.card_ops import Cards, DEFAULT_LIFECYCLE, parse_lifecycle
from crm.desk import Desk
from crm.store import CRMError, Conflict, Forbidden, NotFound, Refused, SharedRefused, Store

ORG = 'acme'
CONTRACT = Path(__file__).parents[1] / 'contracts/crm.v1'
SCHEMA = json.loads((CONTRACT / 'crm.schema.json').read_text())


def conforms(shape, data, case=None):
    errors = list(Draft202012Validator({'$ref': '#/$defs/' + shape, '$defs': SCHEMA['$defs']}).iter_errors(data))
    assert not errors, errors[0].message
    if case and os.environ.get('CRM_WRITE_GOLDENS') == '1':
        (CONTRACT / 'golden' / f'{shape}.{case}.json').write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


class Clock:
    def __init__(self):
        self.at = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
    def __call__(self):
        return self.at


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


@pytest.fixture
def cards(store, clock):
    return Cards(store, clock=clock)


@pytest.fixture
def desk(store, cards, clock):
    return Desk(store, clock=clock, cards=cards)


def person(uid, *workspaces, writable=None, **kw):
    return Caller(subject=uid, email=f'u{uid}@acme.test', workspaces=tuple(workspaces),
                  writable=tuple(workspaces if writable is None else writable), **kw)


ANA = person('7', 'sales', 'ops')
BO = person('8', 'sales')
CY = person('9', 'ops')
VIEWER = person('10', 'sales', writable=())


def agent(uid='7', regime='human', shared=False, session='pchat-1', workspaces=('sales',)):
    return Caller(subject=uid, workspaces=workspaces, writable=workspaces, delegated=True, regime=regime,
                  shared=shared, session=session, trace='t-' + session)


def make(cards, who=ANA, title='Renewal plan', **kw):
    return cards.create(ORG, who, title=title, **kw)['card']


# ── the type, the lifecycle ────────────────────────────────────────────────────────────────────
def test_the_card_type_is_bootstrapped_as_a_card_type_family_member(store):
    with store.engine.connect() as c:
        definition = store._definition(c, ORG, 'Card')
    assert definition.get('x-card') is True
    assert {'Title', 'About', 'State', 'Next', 'Waiting', 'Summary', 'Due', 'Progress', 'Checklist',
            'Parent', 'Face'} <= set(definition['properties'])


def test_the_lifecycle_is_policy_with_a_conservative_default():
    assert parse_lifecycle(None) == DEFAULT_LIFECYCLE == {'stages': ['To do', 'Doing', 'Done'], 'side': ['Dropped']}
    assert parse_lifecycle('Lead > Qualified > Won | Lost | Parked') == {
        'stages': ['Lead', 'Qualified', 'Won'], 'side': ['Lost', 'Parked']}
    assert parse_lifecycle('   ') == DEFAULT_LIFECYCLE
    assert parse_lifecycle('Only') == DEFAULT_LIFECYCLE          # one stage is not a lifecycle


def test_a_company_lifecycle_comes_from_global_policy(store, clock):
    cards = Cards(store, clock=clock, policy_text=lambda: '---\nlifecycles.card: Idea > Build > Ship | Parked\n---\n')
    card = make(cards)
    assert card['state'] == 'Idea' and card['states'] == ['Idea', 'Build', 'Ship'] and card['side_states'] == ['Parked']


# ── a person makes a card ──────────────────────────────────────────────────────────────────────
def test_a_person_makes_a_card_in_its_first_stage_and_is_its_member(cards):
    card = make(cards, about='chat:pchat-1', workspace='sales')
    assert card['title'] == 'Renewal plan' and card['state'] == 'To do'
    assert card['about'] == {'kind': 'chat', 'ref': 'chat:pchat-1'}
    assert card['members'] == [{'subject': '7', 'status': 'member'}]
    assert card['workspaces'] == ['sales'] and card['ref'] == 'card:' + card['id']
    assert card['can'] == {'edit': True, 'transition': True, 'review': True, 'members': True}
    conforms('CardView', card, 'made')


def test_an_agent_cannot_make_a_card_it_proposes_one(cards):
    with pytest.raises(Refused) as e:
        cards.create(ORG, agent(), title='Mine now')
    assert e.value.body['reason'] == 'propose_instead' and 'card_propose' in e.value.body['instruction']


def test_a_card_is_referenced_only_from_workspaces_the_person_can_write(cards):
    with pytest.raises(Forbidden):
        make(cards, who=VIEWER, workspace='sales')
    with pytest.raises(Forbidden):
        make(cards, who=CY, workspace='sales')


def test_about_is_a_typed_reference(cards):
    for bad in ('http://x', 'page:../etc', 'chat:', 'nope:1'):
        with pytest.raises(CRMError):
            make(cards, about=bad)
    assert make(cards, about='page:sales/plans/renewal.md')['about'] == {'kind': 'page', 'ref': 'page:sales/plans/renewal.md'}


# ── the board, membership and the leak rule ───────────────────────────────────────────────────
def test_a_workspace_board_is_the_cards_it_references(cards):
    on_board = make(cards, workspace='sales', title='Board card')
    make(cards, title='Private card')
    board = cards.list(ORG, BO, workspace='sales')
    assert [c['title'] for c in board['cards']] == ['Board card']
    assert cards.list(ORG, CY, workspace='sales')['cards'] == []          # not a member of sales
    assert {c['title'] for c in cards.list(ORG, ANA, mine=True)['cards']} == {'Board card', 'Private card'}
    assert cards.read(ORG, BO, on_board['id'])['can']['edit'] is True    # a sales contributor writes
    assert cards.read(ORG, VIEWER, on_board['id'])['can']['edit'] is False


def test_a_private_card_is_read_by_its_members_only(cards):
    card = make(cards)
    with pytest.raises(NotFound):
        cards.read(ORG, BO, card['id'])
    cards.members(ORG, ANA, card['id'], '8', add=True)
    invited = cards.read(ORG, BO, card['id'])
    assert {'subject': '8', 'status': 'invited'} in invited['members'] and invited['can']['edit'] is False
    cards.membership(ORG, BO, card['id'], accept=True)
    assert cards.read(ORG, BO, card['id'])['can']['edit'] is True


def test_membership_never_grants_what_the_card_is_about(store, cards):
    deal = store.create(ORG, Caller(subject='1', is_admin=True), 'Deal', {'Name': 'Big', 'Amount': 5},
                        reason='fixture', idempotency_key='deal')['record_id']
    card = make(cards, about='crm:' + deal)
    cards.members(ORG, ANA, card['id'], '8', add=True)
    cards.membership(ORG, BO, card['id'], accept=True)
    assert cards.read(ORG, BO, card['id'])['about'] == {'kind': 'crm', 'ref': 'crm:' + deal}
    with pytest.raises(NotFound):
        store.get(ORG, BO, deal)


def test_a_shared_conversation_sees_only_cards_bound_to_its_ceiling(cards):
    private = make(cards)
    with pytest.raises(SharedRefused):
        cards.read(ORG, agent(shared=True), private['id'])


def test_an_invited_person_may_decline_and_a_member_may_leave(cards):
    card = make(cards)
    cards.members(ORG, ANA, card['id'], '8', add=True)
    cards.membership(ORG, BO, card['id'], accept=False)
    with pytest.raises(NotFound):
        cards.read(ORG, BO, card['id'])
    with pytest.raises(Refused):
        cards.members(ORG, agent(), card['id'], '9', add=True)     # an agent never adds a member


# ── stages ─────────────────────────────────────────────────────────────────────────────────────
def test_a_person_moves_a_card_only_to_a_declared_state(cards):
    card = make(cards)
    moved = cards.update(ORG, ANA, card['id'], card['revision'], transition='Doing')['card']
    assert moved['state'] == 'Doing' and moved['movement']['what'] == 'Moved to Doing'
    with pytest.raises(CRMError):
        cards.update(ORG, ANA, card['id'], moved['revision'], transition='Shipped')
    with pytest.raises(CRMError):
        cards.update(ORG, ANA, card['id'], moved['revision'], fields={'State': 'Shipped'})


def test_an_agent_only_proposes_a_stage_and_a_member_accepts_it(store, cards):
    card = make(cards)
    p = cards.update_propose(ORG, agent(), card['id'], card['revision'], transition='Done', reason='the call agreed')
    assert p['status'] == 'proposed'
    assert cards.read(ORG, ANA, card['id'])['state'] == 'To do'
    with pytest.raises(Refused):
        store.review(ORG, agent(), p['proposal_id'], True)          # an agent never accepts
    store.review(ORG, ANA, p['proposal_id'], True)
    after = cards.read(ORG, ANA, card['id'])
    assert after['state'] == 'Done' and after['movement'] == {
        'by': '7', 'kind': 'agent', 'what': 'Moved to Done', 'at': after['movement']['at']}


def test_a_direct_agent_write_of_a_card_stage_is_still_a_proposal(store, cards):
    card = make(cards)
    out = store.change(ORG, agent(), card['id'], card['revision'], {'State': 'Doing'}, reason='x')
    assert out['status'] == 'proposed'


# ── create-as-proposal ─────────────────────────────────────────────────────────────────────────
def test_an_agent_proposes_a_card_into_its_workspace_and_a_member_accepts_it(store, cards):
    p = cards.propose(ORG, agent(uid='8', session='pchat-9'), title='Follow up pricing', workspace='sales',
                      about='meeting:m-1', fields={'Next': 'send the deck'}, reason='promised on the call')
    assert p['status'] == 'proposed'
    assert cards.list(ORG, BO, workspace='sales')['cards'] == []
    pending = cards.list(ORG, ANA, workspace='sales')['proposals']
    assert [x['title'] for x in pending] == ['Follow up pricing'] and pending[0]['kind'] == 'create'
    conforms('CardProposal', pending[0], 'create')
    with pytest.raises(Forbidden):
        store.review(ORG, VIEWER, p['proposal_id'], True)            # a reader of sales cannot accept
    accepted = store.review(ORG, ANA, p['proposal_id'], True)
    card = cards.read(ORG, ANA, accepted['record_id'])
    assert card['title'] == 'Follow up pricing' and card['next'] == 'send the deck' and card['state'] == 'To do'
    assert {m['subject'] for m in card['members']} == {'7', '8'} and card['workspaces'] == ['sales']
    with pytest.raises(Conflict):
        store.review(ORG, ANA, p['proposal_id'], True)


def test_a_card_proposed_outside_its_ceiling_is_refused(cards):
    with pytest.raises(Forbidden):
        cards.propose(ORG, agent(workspaces=('sales',)), title='x', workspace='ops', reason='r')


def test_a_personal_proposal_is_the_persons_own_to_accept(store, cards):
    p = cards.propose(ORG, agent(), title='Read the contract', reason='you asked to remember it')
    assert [x['title'] for x in cards.list(ORG, ANA)['proposals']] == ['Read the contract']
    assert cards.list(ORG, BO)['proposals'] == []
    with pytest.raises(Forbidden):
        store.review(ORG, BO, p['proposal_id'], True)
    rejected = store.review(ORG, ANA, p['proposal_id'], False)
    assert rejected['status'] == 'rejected' and cards.list(ORG, ANA)['proposals'] == []


def test_proposals_are_capped_and_expire(store, cards, clock):
    for n in range(5):
        cards.propose(ORG, agent(), title=f'p{n}', reason='r')
    with pytest.raises(Refused) as e:
        cards.propose(ORG, agent(), title='one too many', reason='r')
    assert e.value.body['reason'] == 'proposals_pending_max'
    clock.at += timedelta(days=8)
    assert cards.list(ORG, ANA)['proposals'] == []
    cards.propose(ORG, agent(), title='fresh', reason='r')


# ── sub-cards ─────────────────────────────────────────────────────────────────────────────────
def test_sub_cards_count_only_children_the_viewer_can_read(cards):
    parent = make(cards, workspace='sales', title='Launch')
    make(cards, workspace='sales', title='Deck', parent=parent['id'])
    done = make(cards, workspace='sales', title='Pricing', parent=parent['id'])
    cards.update(ORG, ANA, done['id'], done['revision'], transition='Done')
    make(cards, title='Private child', parent=parent['id'])
    seen_by_ana = cards.read(ORG, ANA, parent['id'])['children']
    assert (seen_by_ana['done'], seen_by_ana['total']) == (1, 3)
    seen_by_bo = cards.read(ORG, BO, parent['id'])['children']
    assert (seen_by_bo['done'], seen_by_bo['total']) == (1, 2)
    assert {i['title'] for i in seen_by_bo['items']} == {'Deck', 'Pricing'}
    assert cards.read(ORG, BO, done['id'])['parent'] == {'id': parent['id'], 'title': 'Launch'}


# ── faces ──────────────────────────────────────────────────────────────────────────────────────
def test_a_face_is_sanitised_on_the_server(cards):
    face = [{'type': 'stepper'}, {'type': 'script', 'src': 'x'}, {'type': 'metric', 'field': 'Progress', 'label': 'L' * 90},
            {'type': 'text', 'text': 'a literal value an agent painted'}] + [{'type': 'people'}] * 20
    card = make(cards, face=face, fields={'Progress': 0.5})
    types = [b['type'] for b in card['face']]
    assert 'script' not in types and len(card['face']) <= 8
    assert {'type': 'metric', 'label': 'L' * 40, 'value': '50%'} in card['face']
    assert all(b.get('text') != 'a literal value an agent painted' for b in card['face'])


def test_a_face_never_shows_a_field_the_viewer_cannot_read(store, cards):
    with store.engine.begin() as c:
        c.execute(s.policies.insert().values(org_id=ORG, subject_id='8', policy={'active': True, 'objects': {
            'Card': {'read': 'none', 'read_fields': ['Title', 'State', 'Face'], 'write': 'none'}}}))
    card = make(cards, workspace='sales', fields={'Summary': 'margin is 12%'},
                face=[{'type': 'text', 'field': 'Summary', 'label': 'Summary'}])
    assert {'type': 'text', 'label': 'Summary', 'text': 'margin is 12%'} in card['face']
    seen = cards.read(ORG, BO, card['id'])
    assert {'type': 'locked', 'label': 'Summary'} in seen['face'] and seen['summary'] is None


def test_no_face_is_the_default_face(cards):
    assert [b['type'] for b in make(cards)['face']] == ['stepper', 'next', 'waiting', 'movement', 'subcards', 'people']


# ── the desk ────────────────────────────────────────────────────────────────────────────────────
def test_my_desk_holds_the_cards_i_am_on_and_the_ones_i_placed(cards, desk):
    mine = make(cards, title='Mine')
    board = make(cards, who=BO, workspace='sales', title='Bo board')
    d = desk.read(ORG, ANA)
    assert [c['title'] for c in d['cards']] == ['Mine']
    desk.arrange(ORG, ANA, [{'op': 'place', 'ref': board['ref']}], d['version'])
    assert {c['title'] for c in desk.read(ORG, ANA)['cards']} == {'Mine', 'Bo board'}
    v = desk.read(ORG, ANA)['version']
    desk.arrange(ORG, ANA, [{'op': 'remove', 'ref': mine['ref']}], v)
    after = desk.read(ORG, ANA)
    assert [c['title'] for c in after['cards']] == ['Bo board']
    assert cards.read(ORG, ANA, mine['id'])['members'] == [{'subject': '7', 'status': 'member'}]   # card untouched
    conforms('Desk', after, 'with-cards')


def test_a_card_the_person_cannot_read_is_never_placed(cards, desk):
    private = make(cards, who=BO, title='Bo private')
    with pytest.raises(Refused) as e:
        desk.arrange(ORG, ANA, [{'op': 'place', 'ref': private['ref']}], 0)
    assert e.value.body['reason'] == 'not_readable'


def test_invites_and_proposals_wait_on_the_desk(cards, desk):
    card = make(cards, who=BO, title='Shared plan')
    cards.members(ORG, BO, card['id'], '7', add=True)
    cards.propose(ORG, agent(), title='Suggested', reason='r')
    d = desk.read(ORG, ANA)
    assert [c['title'] for c in d['card_invites']] == ['Shared plan']
    assert [p['title'] for p in d['card_proposals']] == ['Suggested']
    assert d['cards'] == []


def test_cards_switched_off_keep_the_d1_desk(store, clock, monkeypatch):
    monkeypatch.setenv('VEXA_FEATURES', 'crm,desktop')
    d = Desk(store, clock=clock, cards=Cards(store, clock=clock))
    read = d.read(ORG, ANA)
    assert 'cards' not in read
    with pytest.raises(Refused) as e:
        d.arrange(ORG, ANA, [{'op': 'place', 'ref': 'card:abc'}], 0)
    assert e.value.body['reason'] == 'ref_kind_not_enabled'


# ── history ────────────────────────────────────────────────────────────────────────────────────
def test_activity_is_the_cards_revisions(cards):
    card = make(cards)
    c2 = cards.update(ORG, ANA, card['id'], card['revision'], fields={'Next': 'call Bo'})['card']
    cards.update(ORG, ANA, card['id'], c2['revision'], transition='Doing')
    activity = cards.read(ORG, ANA, card['id'])['activity']
    assert [a['what'] for a in activity] == ['Moved to Doing', 'Next: call Bo', 'Made the card']


# ── the migration ─────────────────────────────────────────────────────────────────────────────
def test_an_existing_v3_database_upgrades_to_v4_with_card_members():
    from sqlalchemy import inspect, text
    from crm import migrate
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    with engine.begin() as c:
        for table in s.metadata.sorted_tables:
            if table.name != 'crm_card_members':
                table.create(c)
        c.execute(text('INSERT INTO crm_schema (version) VALUES (3)'))
    with engine.begin() as c:
        migrate.tables_and_version(c)
    assert 'crm_card_members' in set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert c.execute(text('SELECT version FROM crm_schema')).scalar_one() == s.SCHEMA_VERSION >= 4   # 5: workspace settings
    assert migrate.WRITES['crm_card_members'] == 'INSERT, UPDATE, DELETE'
    assert s.change_proposals.c.record_id.nullable          # a proposed card names no record yet


# ── the face case table (blocks.v1 golden FaceCases, held identical by parity fact card-face-cases) ──
FACE_CASES = json.loads((Path(__file__).parent / 'fixtures/face-cases.json').read_text())


@pytest.mark.parametrize('case', FACE_CASES, ids=[c['name'] for c in FACE_CASES])
def test_card_face_resolves_the_shared_case_table(case):
    from crm import card_face
    assert card_face.resolve(case['face'], case['fields'], case['readable'], case.get('formats')) == case['expect']


# ── stalled is policy; "new" clears when the viewer opens the card ───────────────────────────
def test_stalled_follows_the_global_policy_and_defaults_to_fourteen_days(store, clock):
    default = Cards(store, clock=clock)
    card = make(default)
    clock.at += timedelta(days=13)
    assert default.read(ORG, ANA, card['id'])['stalled'] is False
    clock.at += timedelta(days=2)
    assert default.read(ORG, ANA, card['id'])['stalled'] is True
    narrowed = Cards(store, clock=clock, policy_text=lambda: '---\ncard_stalled_days: 3\n---\n')
    fresh = make(narrowed, title='fresh')
    clock.at += timedelta(days=4)
    assert narrowed.read(ORG, ANA, fresh['id'])['stalled'] is True
    for bad in ('0', 'soon', '-2'):
        odd = Cards(store, clock=clock, policy_text=lambda b=bad: f'---\ncard_stalled_days: {b}\n---\n')
        assert odd.stalled_after() == timedelta(days=14)


def test_new_clears_when_the_viewer_opens_the_card(cards):
    card = make(cards, workspace='sales', title='Board card')
    assert cards.list(ORG, ANA, workspace='sales')['cards'][0]['new'] is False      # my own card
    assert cards.list(ORG, BO, workspace='sales')['cards'][0]['new'] is True
    cards.list(ORG, BO, workspace='sales')                                          # listing is not opening
    assert cards.list(ORG, BO, workspace='sales')['cards'][0]['new'] is True
    cards.read(ORG, agent(uid='8'), card['id'])                                     # an agent reading is not the person opening
    assert cards.list(ORG, BO, workspace='sales')['cards'][0]['new'] is True
    cards.read(ORG, BO, card['id'])
    assert cards.list(ORG, BO, workspace='sales')['cards'][0]['new'] is False
