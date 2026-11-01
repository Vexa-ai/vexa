"""Who writes which card field (founder, 1 Nov 2026): ``card_field_writers.<Field>`` in ``_global/POLICIES.md``.

The factory's policy: State only by the stage rule, on every card; Kind, Source and Chain set once
when a card is made, then only by the founder; Priority and Destination only by the founder; Next,
Summary and Waiting by anyone the CRM policy lets write the card. Checked on every write path (the
card verbs, ``crm_change``, a proposal's acceptance, the generic create), an administrator's too.
Offline: SQLite.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.bootstrap import bootstrap_org
from crm.caller import Caller
from crm.card_ops import Cards, PolicyText
from crm.store import CRMError, Refused, Store

ORG = 'acme'
FOUNDER = '900004'
POLICY = f"""---
card_field_writers.state: rule
card_field_writers.next: anyone
card_field_writers.summary: anyone
card_field_writers.waiting: anyone
card_field_writers.priority: {FOUNDER}
card_field_writers.destination: {FOUNDER}
card_field_writers.*: create, {FOUNDER}
---
"""
PR = 'https://github.com/Vexa-ai/vexa/pull/1962'
OTHER_PR = 'https://github.com/Vexa-ai/vexa/pull/1995'


def person(subject, admin=False):
    return Caller(subject=subject, email=f'u{subject}@acme.test', workspaces=('sales',), writable=('sales',),
                  is_admin=admin)


CEO, SOFTWARE, BUSINESS = person('900001', admin=True), person('900002', admin=True), person('900003', admin=True)
AGENTS = (CEO, SOFTWARE, BUSINESS)
THE_FOUNDER = person(FOUNDER, admin=True)
DELEGATED = Caller(subject=FOUNDER, workspaces=('sales',), writable=('sales',), delegated=True, regime='autonomous',
                   session='s1', credential_class='delegation')


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    st = Store(engine)
    # the install's own fields (the factory's definitions.json): Priority and Destination
    with st.begin() as c:
        d = st._definition(c, ORG, 'Card')
        props = {**d['properties'], 'Priority': {'type': 'integer'}, 'Destination': {'type': 'string'}}
        c.execute(s.object_types.update().where(s.object_types.c.org_id == ORG, s.object_types.c.name == 'Card')
                  .values(definition={**d, 'properties': props}))
    return st


@pytest.fixture
def cards(store):
    return Cards(store, clock=lambda: datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc), policy_text=lambda: POLICY)


def plain(cards, who=CEO, **fields):
    return cards.create(ORG, who, title='Reply to the venue', workspace='sales', fields=fields)['card']


def software(cards, who=SOFTWARE):
    return cards.create(ORG, who, title='Scheduled bots', workspace='sales',
                        fields={'Kind': 'software', 'Source': PR, 'Chain': {'blocked_by': []}})['card']


def fields_of(store, rid):
    with store.engine.connect() as c:
        return store._row(c, ORG, rid)['fields']


# ── agents ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('agent', AGENTS, ids=lambda a: a.subject)
def test_an_agent_makes_a_card_with_its_kind_source_and_chain_once(cards, store, agent):
    card = cards.create(ORG, agent, title='Port #1977', workspace='sales',
                        fields={'Kind': 'software', 'Source': PR, 'Chain': {'blocked_by': ['c1']},
                                'Next': 'open the PR', 'Waiting': 'software'})['card']
    got = fields_of(store, card['id'])
    assert got['Kind'] == 'software' and got['Source'] == PR and got['Chain']['blocked_by'] == ['c1']
    assert got['State'] == 'Idea'


@pytest.mark.parametrize('agent', AGENTS, ids=lambda a: a.subject)
def test_an_agent_writes_next_summary_and_waiting(cards, store, agent):
    card = plain(cards)
    out = cards.update(ORG, agent, card['id'], card['revision'],
                       fields={'Next': 'send the draft', 'Summary': 'GitHub: Vexa-ai/vexa#1', 'Waiting': 'founder'})
    got = fields_of(store, card['id'])
    assert out['status'] == 'saved' and got['Next'] == 'send the draft' and got['Waiting'] == 'founder'
    rev = out['card']['revision']
    assert store.change(ORG, agent, card['id'], rev, {'Summary': 'none'})['status'] == 'saved'


@pytest.mark.parametrize('field,value', [
    ('Kind', 'software'), ('Source', OTHER_PR), ('Chain', {'blocked_by': ['x9']}), ('Title', 'A new title'),
    ('About', 'crm:abc'), ('Due', '2026-11-08')])
@pytest.mark.parametrize('agent', AGENTS, ids=lambda a: a.subject)
def test_an_agent_may_not_change_a_locked_field(cards, store, agent, field, value):
    card = software(cards) if field in ('Source', 'Chain') else plain(cards)
    before = fields_of(store, card['id'])
    with pytest.raises(Refused) as e:
        cards.update(ORG, agent, card['id'], card['revision'], fields={field: value})
    assert e.value.body['reason'] == 'field_writer' and e.value.body['field'] == field
    assert e.value.body['writers'] == ['create', FOUNDER]
    with pytest.raises(Refused):
        store.change(ORG, agent, card['id'], card['revision'], {field: value})
    assert fields_of(store, card['id']) == before


@pytest.mark.parametrize('field,value', [('Priority', 1), ('Destination', 'told OeNB')])
@pytest.mark.parametrize('agent', AGENTS, ids=lambda a: a.subject)
def test_an_agent_may_not_set_priority_or_destination_even_when_making_the_card(cards, store, agent, field, value):
    with pytest.raises(Refused) as e:
        store.create(ORG, agent, 'Card', {'Title': 'x', field: value})
    assert e.value.body == {**e.value.body, 'reason': 'field_writer', 'field': field, 'writers': [FOUNDER]}
    card = plain(cards)
    with pytest.raises(Refused):
        store.change(ORG, agent, card['id'], card['revision'], {field: value})


@pytest.mark.parametrize('agent', AGENTS, ids=lambda a: a.subject)
def test_an_agent_moves_no_card_plain_or_software(cards, store, agent):
    for card in (plain(cards), software(cards)):
        to = 'Done'
        with pytest.raises(Refused) as e:
            cards.update(ORG, agent, card['id'], card['revision'], transition=to)
        assert e.value.body['reason'] == 'stage_from_facts'
        with pytest.raises(Refused):
            store.change(ORG, agent, card['id'], card['revision'], {'State': to})
    with pytest.raises(Refused):
        store.create(ORG, agent, 'Card', {'Title': 'Born doing', 'State': 'Doing'})


def test_a_value_the_card_already_holds_is_no_write(cards, store):
    card = software(cards)
    f = fields_of(store, card['id'])
    out = store.change(ORG, CEO, card['id'], card['revision'],
                       {'Kind': f['Kind'], 'Source': f['Source'], 'State': f['State'], 'Next': 'n'})
    assert out['status'] == 'saved'


# ── the founder ──────────────────────────────────────────────────────────────────────────────

def test_the_founder_writes_kind_source_chain_priority_destination(cards, store):
    card = plain(cards)
    rev = store.change(ORG, THE_FOUNDER, card['id'], card['revision'],
                       {'Priority': 1, 'Destination': 'told OeNB', 'Chain': {'blocked_by': ['c2']},
                        'Source': PR, 'Title': 'Reply to the venue today'})['revision']
    got = fields_of(store, card['id'])
    assert got['Priority'] == 1 and got['Destination'] == 'told OeNB' and got['Chain']['blocked_by'] == ['c2']
    assert got['Source'] == PR
    sw = software(cards)
    assert store.change(ORG, THE_FOUNDER, sw['id'], sw['revision'], {'Source': OTHER_PR})['status'] == 'saved'
    assert rev == card['revision'] + 1
    made = store.create(ORG, THE_FOUNDER, 'Card', {'Title': 'Goal', 'Priority': 2})
    assert fields_of(store, made['record_id'])['Priority'] == 2


def test_the_founder_moves_no_stage_either(cards, store):
    for card in (plain(cards), software(cards)):
        with pytest.raises(Refused) as e:
            store.change(ORG, THE_FOUNDER, card['id'], card['revision'], {'State': 'Dropped'})
        assert e.value.body['reason'] == 'stage_from_facts'
        with pytest.raises(Refused):
            cards.update(ORG, THE_FOUNDER, card['id'], card['revision'], transition='Done')


def test_a_proposal_is_checked_against_whoever_accepts_it(cards, store):
    card = plain(cards, who=THE_FOUNDER)
    p = store.change(ORG, CEO, card['id'], card['revision'], {'Priority': 3}, reason='r', propose=True)
    assert p['status'] == 'proposed'            # filing changes nothing
    store.review(ORG, THE_FOUNDER, p['proposal_id'], True)
    assert fields_of(store, card['id'])['Priority'] == 3
    card = cards.read(ORG, THE_FOUNDER, card['id'])
    p = store.change(ORG, CEO, card['id'], card['revision'], {'Title': 'Renamed'}, reason='r', propose=True)
    with pytest.raises(Refused):                # an agent accepting another agent's change is still an agent
        store.review(ORG, BUSINESS, p['proposal_id'], True)
    p = store.change(ORG, CEO, card['id'], card['revision'], {'State': 'Done'}, reason='r', propose=True)
    with pytest.raises(Refused) as e:           # nobody's acceptance moves a stage
        store.review(ORG, THE_FOUNDER, p['proposal_id'], True)
    assert e.value.body['reason'] == 'stage_from_facts'
    assert fields_of(store, card['id'])['State'] == 'To do'


def test_a_delegated_agent_acting_for_the_founder_still_only_proposes(cards, store):
    card = plain(cards, who=THE_FOUNDER)
    out = store.change(ORG, DELEGATED, card['id'], card['revision'], {'Priority': 5}, reason='r')
    assert out['status'] == 'proposed' and 'Priority' not in fields_of(store, card['id'])


# ── the stage rule ───────────────────────────────────────────────────────────────────────────

def test_the_rule_moves_every_card_and_writes_nothing_else(cards, store):
    card = plain(cards)
    out = cards.set_stage(ORG, card['id'], 'Doing', 'claimed by business-worker', [{'fact': 'claim', 'value': {}}])
    assert out['status'] == 'moved' and fields_of(store, card['id'])['State'] == 'Doing'
    with pytest.raises(CRMError):
        cards.set_stage(ORG, card['id'], 'Review', 'not a stage of this card')
    sw = software(cards)
    assert cards.set_stage(ORG, sw['id'], 'Review', '#1962 open', [{'fact': 'pr', 'value': {}}])['status'] == 'moved'
    cards._rule.on = True
    try:
        with pytest.raises(CRMError):           # the rule writes State and StageEvidence only
            cards.normalise_fields('Card', {'Priority': 1}, fields_of(store, card['id']))
    finally:
        cards._rule.on = False


def test_without_the_policy_line_the_rule_moves_software_cards_only(store):
    cards = Cards(store, policy_text=lambda: None)
    card = cards.create(ORG, CEO, title='plain', workspace='sales')['card']
    with pytest.raises(CRMError):
        cards.set_stage(ORG, card['id'], 'Doing', 'claimed')
    # and with no policy lines the fields are as the CRM policy has them (today's behaviour)
    assert cards.update(ORG, CEO, card['id'], card['revision'], fields={'Title': 'x'})['status'] == 'saved'


# ── the policy door ──────────────────────────────────────────────────────────────────────────

class Door:
    def __init__(self):
        self.text, self.fail = POLICY, False

    def fetch(self):
        if self.fail:
            raise OSError('door down')
        return self.text


def test_a_door_that_stops_answering_keeps_the_lock():
    door, now = Door(), [0.0]
    text = PolicyText(door, ttl=1.0, now=lambda: now[0])
    assert text() == POLICY
    door.fail, now[0] = True, 5.0
    assert text() == POLICY                       # a failed read does not open the lock
    door.fail, door.text, now[0] = False, None, 10.0
    assert text() == POLICY                       # nor does a missing answer
    door.text, now[0] = '---\n---\n', 15.0
    assert text() == '---\n---\n'                 # an answered change applies



# ── one stored edge, and who wrote what (founder, 1 Nov 2026) ────────────────────────────────

def test_chain_stores_blocked_by_only_and_blocks_is_computed(cards, store):
    first = plain(cards)
    then = cards.create(ORG, CEO, title='After it', workspace='sales',
                        fields={'Chain': {'blocked_by': [first['id']]}})['card']
    assert fields_of(store, then['id'])['Chain'] == {'blocked_by': [first['id']]}
    with pytest.raises(CRMError):
        cards.create(ORG, CEO, title='Says it blocks', workspace='sales', fields={'Chain': {'blocks': [first['id']]}})
    with store.engine.connect() as c:
        view = cards.view(c, ORG, store._policy(c, ORG, CEO), cards._card_row(c, ORG, first['id']))
    assert view['chain']['blocks'] == [then['id']] and view['chain']['blocked_by'] == []


def test_history_names_who_wrote_each_revision(cards, store):
    card = plain(cards, who=BUSINESS)
    cards.update(ORG, CEO, card['id'], fields_of_rev(store, card['id']), fields={'Next': 'call them'})
    revs = store.history(ORG, CEO, card['id'])['revisions']
    assert [r['actor_id'] for r in revs] == [CEO.subject, BUSINESS.subject]


def fields_of_rev(store, rid):
    with store.engine.connect() as c:
        return store._row(c, ORG, rid)['revision']
