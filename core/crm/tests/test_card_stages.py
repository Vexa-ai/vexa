"""A software card's stage is set from facts by the stage rule only (founder, 1 Nov 2026).

The ``software`` kind (``card_templates.py``) moves ``Idea > Design > Code > Review > On prod > In main >
Released > Done``, side ``Dropped``; ``Cards.set_stage`` (``/internal/cards/stage``) is the only
writer of its State and ``StageEvidence``. Every other path — a person's edit, an agent's edit or
proposal, ``crm_change``, a gate — is refused. Readings are kept per source. Offline: SQLite.
"""
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.api import Identity, create_app
from crm.bootstrap import CARD_LIFECYCLE_PROPERTIES, PRIOR_CARD_PROPERTIES, bootstrap_org, upgrade_card_definition
from crm.caller import Caller
from crm.card_ops import STAGE_RULE_ACTOR, Cards
from crm.store import CRMError, Refused, Store

ORG = 'acme'
ANA = Caller(subject='7', email='u7@acme.test', workspaces=('sales',), writable=('sales',))
AGENT = Caller(subject='7', workspaces=('sales',), writable=('sales',), delegated=True, regime='autonomous',
               session='s1', credential_class='delegation')
PR = 'https://github.com/Vexa-ai/vexa/pull/1962'
STAGES = ['Idea', 'Design', 'Code', 'Review', 'On prod', 'In main', 'Released', 'Done']
FACT = {'fact': 'pr', 'value': {'state': 'open', 'head_sha': 'abc1234'}, 'source': 'github/delivery',
        'read_at': 1_793_500_000.0}


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
def cards(store):
    return Cards(store, clock=lambda: datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))


def software(cards, title='Scheduled bots on time', **fields):
    return cards.create(ORG, ANA, title=title, workspace='sales',
                        fields={'Kind': 'software', 'Source': PR, **fields})['card']


def revisions(store, record):
    with store.engine.connect() as c:
        return c.execute(select(s.revisions.c.revision, s.revisions.c.actor_id, s.revisions.c.reason,
                                s.revisions.c.evidence)
                         .where(s.revisions.c.org_id == ORG, s.revisions.c.record_id == record)
                         .order_by(s.revisions.c.revision)).all()


def test_a_software_card_has_the_delivery_lifecycle_and_starts_at_idea(cards):
    card = software(cards)
    assert card['state'] == 'Idea' and card['states'] == STAGES and card['side_states'] == ['Dropped']
    assert card['kind'] == 'software' and card['can']['transition'] is False
    assert card['next_actor'] is None and card['viewer']['actions'] == []    # no gates, no next actor
    assert card['stage_evidence'] is None


def test_nobody_moves_a_software_card_by_hand(cards, store):
    card = software(cards)
    rid, rev = card['id'], card['revision']
    with pytest.raises(Refused) as e:                        # a person, by the card verb
        cards.update(ORG, ANA, rid, rev, transition='Review')
    assert e.value.body['reason'] == 'stage_from_facts'
    with pytest.raises(Refused):                              # a person, by crm_change
        store.change(ORG, ANA, rid, rev, {'State': 'Done'})
    with pytest.raises(Refused):                              # an agent, editing
        cards.update(ORG, AGENT, rid, rev, transition='Review')
    with pytest.raises(Refused) as e:                         # an agent, proposing a move
        cards.update_propose(ORG, AGENT, rid, rev, transition='Done', reason='it shipped')
    assert e.value.body['reason'] == 'stage_from_facts'
    with pytest.raises(CRMError):                             # an agent, by crm_change proposal
        store.change(ORG, AGENT, rid, rev, {'State': 'Done'}, reason='r', propose=True)
    with pytest.raises(CRMError):                             # born past its first stage
        cards.create(ORG, ANA, title='Born done', workspace='sales', fields={'Kind': 'software', 'State': 'Done'})
    with pytest.raises(Refused):                              # the write-path hook itself, for a new record
        cards.normalise_fields('Card', {'Title': 'Born done', 'Kind': 'software', 'State': 'Done'}, None)
    for who, kw in ((ANA, {}), (AGENT, {'reason': 'r', 'propose': True})):
        with pytest.raises(CRMError):                         # the evidence is the rule's
            store.change(ORG, who, rid, rev, {'StageEvidence': {'stage': 'Done'}}, **kw)
    with pytest.raises(Exception):                            # no gate moves it either
        cards.decide(ORG, ANA, rid, 'deliver', 'n' * 20, '0' * 64)
    got = cards.read(ORG, ANA, rid)
    assert got['state'] == 'Idea' and got['revision'] == rev
    # other fields stay a person's to edit
    assert cards.update(ORG, ANA, rid, rev, fields={'Next': 'review it'})['card']['next'] == 'review it'


def test_the_rule_moves_it_with_the_fact_that_moved_it(cards, store):
    card = software(cards)
    out = cards.set_stage(ORG, card['id'], 'Review', '#1962 is open for review at abc1234', [FACT])
    assert out['status'] == 'moved' and out['was'] == 'Idea' and out['revision'] == card['revision'] + 1
    got = cards.read(ORG, ANA, card['id'])
    assert got['state'] == 'Review'
    ev = got['stage_evidence']
    assert ev['stage'] == 'Review' and ev['because'] == '#1962 is open for review at abc1234'
    assert ev['facts'] == [FACT] and ev['by'] == STAGE_RULE_ACTOR and ev['mode'] == 'event' and ev['was'] == 'Idea'
    assert 'correction' not in ev
    last = revisions(store, card['id'])[-1]
    assert last.actor_id == STAGE_RULE_ACTOR and last.reason == 'Moved to Review: #1962 is open for review at abc1234'
    # the same stage again writes nothing
    n = len(revisions(store, card['id']))
    assert cards.set_stage(ORG, card['id'], 'Review', 'still open', [FACT])['status'] == 'unchanged'
    assert len(revisions(store, card['id'])) == n


def test_a_recheck_that_moves_a_card_marks_a_readable_correction(cards, store):
    card = software(cards)
    cards.set_stage(ORG, card['id'], 'On prod', 'the prod pin contains head abc1234', [FACT])
    out = cards.set_stage(ORG, card['id'], 'Review', 'the prod pin no longer contains head abc1234', [FACT],
                          mode='recheck')
    assert out['status'] == 'corrected'
    ev = cards.read(ORG, ANA, card['id'])['stage_evidence']
    assert ev['mode'] == 'recheck'
    assert ev['correction'] == 'was On prod, now Review, because the prod pin no longer contains head abc1234'
    assert revisions(store, card['id'])[-1].reason.startswith('Corrected: was On prod, now Review')


def test_the_rule_writes_only_software_cards_and_only_their_stages(cards):
    plain = cards.create(ORG, ANA, title='Plain', workspace='sales')['card']
    with pytest.raises(CRMError):
        cards.set_stage(ORG, plain['id'], 'Done', 'x')
    card = software(cards)
    for stage, because, mode in (('Doing', 'x', 'event'), ('Done', '', 'event'), ('Done', 'x', 'later')):
        with pytest.raises(CRMError):
            cards.set_stage(ORG, card['id'], stage, because, mode=mode)
    assert cards.set_stage(ORG, card['id'], 'Dropped', 'the issue was closed as not planned')['status'] == 'moved'


def test_a_plain_card_made_software_starts_at_idea(cards):
    plain = cards.create(ORG, ANA, title='Was plain', workspace='sales')['card']
    got = cards.update(ORG, ANA, plain['id'], plain['revision'], transition='Doing')['card']
    assert got['state'] == 'Doing'
    got = cards.update(ORG, ANA, got['id'], got['revision'], fields={'Kind': 'software', 'Source': PR})['card']
    assert got['state'] == 'Idea' and got['kind'] == 'software'


def test_software_cards_lists_the_open_ones_with_their_readings(cards):
    a, b = software(cards, 'A'), software(cards, 'B')
    software(cards, 'C')
    cards.create(ORG, ANA, title='Plain', workspace='sales')
    cards.set_stage(ORG, b['id'], 'Done', 'released in v0.13.4')
    cards.write_reading(ORG, a['id'], 'github/vexa-pulls', {'state': 'open', 'head_sha': 'abc1234'},
                        {'read_at': 1_793_500_000.0})
    out = cards.software_cards(ORG)
    assert out['stages'] == STAGES and out['side'] == ['Dropped']
    got = {c['id']: c for c in out['cards']}
    assert a['id'] in got and b['id'] not in got and len(got) == 2        # Done is not open; plain is not software
    assert got[a['id']]['source'] == PR and got[a['id']]['reading']['fields']['state'] == 'open'
    one = cards.software_cards(ORG, record=b['id'])['cards']
    assert [c['state'] for c in one] == ['Done']                          # one card, open or not


def test_readings_are_kept_per_source_and_merged(cards):
    card = software(cards)
    t0 = 1_793_500_000.0
    cards.write_reading(ORG, card['id'], 'github/vexa-pulls',
                        {'state': 'open', 'head_sha': 'abc1234', 'title': 'Ramp'}, {'read_at': t0})
    delivery = {'pr': {'url': PR, 'number': 1962, 'state': 'open', 'draft': False, 'head_sha': 'abc1234'},
                'checks': {'status': 'pending', 'sha': 'abc1234', 'total': 3, 'running': 1, 'failed': 0, 'passed': 2},
                'prod': {'contains': True, 'chart': '0.1.0-estate.20261031.core0134rc2.3', 'entry_seq': 58,
                         'source_sha': '090be9bce9f1a868a5aebff393d1183343a324c4', 'commit': 'abc1234', 'via': 'head'},
                'release': None}
    assert cards.write_reading(ORG, card['id'], 'github/delivery', delivery, {'read_at': t0 + 5})['status'] == 'applied'
    # the list sensor reads the pull request again: it replaces its own entry, never the delivery one
    assert cards.write_reading(ORG, card['id'], 'github/vexa-pulls',
                               {'state': 'open', 'head_sha': 'def5678', 'title': 'Ramp'}, {'read_at': t0 + 10})['status'] == 'applied'
    r = cards.read(ORG, ANA, card['id'])['reading']
    assert set(r['sources']) == {'github/vexa-pulls', 'github/delivery'}
    assert r['sources']['github/delivery']['fields']['prod']['contains'] is True
    assert r['fields']['head_sha'] == 'def5678' and r['fields']['checks']['status'] == 'pending'
    assert r['evidence']['source'] == 'github/vexa-pulls' and r['evidence']['read_at'] == t0 + 10
    # stale is per source: an older delivery reading changes nothing
    assert cards.write_reading(ORG, card['id'], 'github/delivery', delivery, {'read_at': t0})['status'] == 'stale'


def test_delivery_fields_are_checked_to_their_shape(cards):
    card = software(cards)
    ev = {'read_at': 1_793_500_000.0}
    for bad in ({'checks': {'status': 'green'}}, {'prod': {'contains': 'yes'}}, {'pr': {'title': 'free text'}},
                {'pr': 'open'}, {'release': {'tag': 'x' * 101}}, {'state_reason': 'abandoned'},
                {'draft': 'no'}, {'merge_commit_sha': 'main'}, {'prod': {'entry_seq': -1}}):
        with pytest.raises(CRMError):
            cards.write_reading(ORG, card['id'], 'github/delivery', bad, ev)


def test_an_existing_org_gains_the_software_kind_unless_an_administrator_changed_kind(store):
    with store.engine.begin() as c:
        row = c.execute(select(s.object_types.c.definition).where(
            s.object_types.c.org_id == ORG, s.object_types.c.name == 'Card')).scalar_one()
        props = {k: v for k, v in row['properties'].items() if k != 'StageEvidence'}
        props['Kind'] = PRIOR_CARD_PROPERTIES['Kind'][0]
        props['Reading'] = PRIOR_CARD_PROPERTIES['Reading'][0]
        c.execute(s.object_types.update().where(s.object_types.c.org_id == ORG, s.object_types.c.name == 'Card')
                  .values(definition={**row, 'properties': props}))
    assert upgrade_card_definition(store.engine, ORG) is True
    with store.engine.connect() as c:
        props = c.execute(select(s.object_types.c.definition).where(
            s.object_types.c.org_id == ORG, s.object_types.c.name == 'Card')).scalar_one()['properties']
    assert props['Kind'] == CARD_LIFECYCLE_PROPERTIES['Kind'] and 'software' in props['Kind']['enum']
    assert 'sources' in props['Reading']['properties'] and 'StageEvidence' in props
    # an administrator's own Kind stays as they wrote it
    with store.engine.begin() as c:
        row = c.execute(select(s.object_types.c.definition).where(
            s.object_types.c.org_id == ORG, s.object_types.c.name == 'Card')).scalar_one()
        mine = {'type': ['string', 'null'], 'enum': ['ask', None]}
        c.execute(s.object_types.update().where(s.object_types.c.org_id == ORG, s.object_types.c.name == 'Card')
                  .values(definition={**row, 'properties': {**row['properties'], 'Kind': mine}}))
    assert upgrade_card_definition(store.engine, ORG) is False


def test_the_stage_routes_are_internal_tier_only(store, cards):
    card = software(cards)
    client = TestClient(create_app(store, Identity('http://identity', 's', httpx.MockTransport(
        lambda r: httpx.Response(401))), ORG, internal_secret='internal-s'))
    hdr = {'X-Internal-Secret': 'internal-s'}
    stage = {'record': card['id'], 'stage': 'Review', 'because': '#1962 is open', 'facts': [FACT]}
    for path, b in (('/internal/cards/stage', stage), ('/internal/cards/software', {})):
        assert client.post(path, json=b).status_code == 403
        assert client.post(path, json=b, headers={'X-Internal-Secret': 'wrong'}).status_code == 403
        assert client.post(path, json=b, headers={'Authorization': 'Bearer a-person-token'}).status_code in (401, 403)
    r = client.post('/internal/cards/software', json={}, headers=hdr)
    assert r.status_code == 200 and [c['id'] for c in r.json()['cards']] == [card['id']]
    r = client.post('/internal/cards/stage', json=stage, headers=hdr)
    assert r.status_code == 200 and r.json()['status'] == 'moved', r.text
    assert client.post('/internal/cards/stage', json=stage, headers=hdr).json()['status'] == 'unchanged'
    r = client.post('/internal/cards/stage', json={**stage, 'stage': 'Done', 'mode': 'recheck'}, headers=hdr)
    assert r.json()['status'] == 'corrected'
    assert 400 <= client.post('/internal/cards/stage', json={**stage, 'stage': 'Doing'}, headers=hdr).status_code < 500
