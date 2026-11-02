"""A software card's stage is set from facts by the stage rule only (founder, 1 Nov 2026).

The ``software`` kind (``card_templates.py``) moves ``Idea > Design > Code > Review > On prod >
Done``, side ``Dropped``; ``Cards.set_stage`` (``/internal/cards/stage``) is the only
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
from crm.store import CRMError, Forbidden, Refused, Store

ORG = 'acme'
ANA = Caller(subject='7', email='u7@acme.test', workspaces=('sales',), writable=('sales',))
AGENT = Caller(subject='7', workspaces=('sales',), writable=('sales',), delegated=True, regime='autonomous',
               session='s1', credential_class='delegation')
PR = 'https://github.com/Vexa-ai/vexa/pull/1962'
STAGES = ['Idea', 'Design', 'Code', 'Review', 'On prod', 'In main']
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
        store.change(ORG, ANA, rid, rev, {'State': 'In main'})
    with pytest.raises(Refused):                              # an agent, editing
        cards.update(ORG, AGENT, rid, rev, transition='Review')
    with pytest.raises(Refused) as e:                         # an agent, proposing a move
        cards.update_propose(ORG, AGENT, rid, rev, transition='In main', reason='it shipped')
    assert e.value.body['reason'] == 'stage_from_facts'
    with pytest.raises(CRMError):                             # an agent, by crm_change proposal
        store.change(ORG, AGENT, rid, rev, {'State': 'In main'}, reason='r', propose=True)
    with pytest.raises(CRMError):                             # born past its first stage
        cards.create(ORG, ANA, title='Born done', workspace='sales', fields={'Kind': 'software', 'State': 'In main'})
    with pytest.raises(Refused):                              # the write-path hook itself, for a new record
        cards.normalise_fields('Card', {'Title': 'Born done', 'Kind': 'software', 'State': 'In main'}, None)
    for who, kw in ((ANA, {}), (AGENT, {'reason': 'r', 'propose': True})):
        with pytest.raises(CRMError):                         # the evidence is the rule's
            store.change(ORG, who, rid, rev, {'StageEvidence': {'stage': 'In main'}}, **kw)
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
    pinned = {**FACT, 'fact': 'prod', 'value': {'contains': True, 'commit': 'abc1234'}}
    cards.set_stage(ORG, card['id'], 'On prod', 'the prod pin contains head abc1234', [FACT, pinned])
    gone = {**pinned, 'value': {'contains': False, 'commit': 'abc1234'}}
    out = cards.set_stage(ORG, card['id'], 'Review', 'the prod pin no longer contains head abc1234', [FACT, gone],
                          mode='recheck')
    assert out['status'] == 'corrected'
    ev = cards.read(ORG, ANA, card['id'])['stage_evidence']
    assert ev['mode'] == 'recheck'
    assert ev['correction'] == 'was On prod, now Review, because the prod pin no longer contains head abc1234'
    assert revisions(store, card['id'])[-1].reason.startswith('Corrected: was On prod, now Review')


def test_the_rule_writes_only_software_cards_and_only_their_stages(cards):
    plain = cards.create(ORG, ANA, title='Plain', workspace='sales')['card']
    with pytest.raises(CRMError):
        cards.set_stage(ORG, plain['id'], 'In main', 'x')
    card = software(cards)
    for stage, because, mode in (('Doing', 'x', 'event'), ('In main', '', 'event'), ('In main', 'x', 'later')):
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
    cards.set_stage(ORG, b['id'], 'In main', 'released in v0.13.4')
    cards.write_reading(ORG, a['id'], 'github/vexa-pulls', {'state': 'open', 'head_sha': 'abc1234'},
                        {'read_at': 1_793_500_000.0})
    out = cards.software_cards(ORG)
    assert out['stages'] == STAGES and out['side'] == ['Dropped']
    got = {c['id']: c for c in out['cards']}
    assert a['id'] in got and b['id'] not in got and len(got) == 2        # Done is not open; plain is not software
    assert got[a['id']]['source'] == PR and got[a['id']]['reading']['fields']['state'] == 'open'
    one = cards.software_cards(ORG, record=b['id'])['cards']
    assert [c['state'] for c in one] == ['In main']                          # one card, open or not


def test_a_done_card_merged_outside_main_is_read_again(cards):
    """seq61 (2 Nov): a card Done from a merge into codex/v0.13.4 must reach the stage rule again; one merged into
    main, or Done for another reason, does not."""
    a, b, c, d = (software(cards, t) for t in ('A', 'B', 'C', 'D'))
    pr = lambda **k: [{'fact': 'pr', 'value': {'url': PR, 'state': 'merged', **k}}]       # noqa: E731
    cards.set_stage(ORG, a['id'], 'In main', '#1 was merged', facts=pr(base='codex/v0.13.4', default_branch='main'))
    cards.set_stage(ORG, b['id'], 'In main', '#2 was merged', facts=pr(base='main', default_branch='main'))
    cards.set_stage(ORG, c['id'], 'In main', '#3 was merged', facts=pr())                      # read before base was
    cards.set_stage(ORG, d['id'], 'In main', 'the issue was closed as completed')
    got = {x['id'] for x in cards.software_cards(ORG)['cards']}
    assert a['id'] in got and c['id'] in got and b['id'] not in got and d['id'] not in got


def test_a_pull_request_that_names_a_card_finds_it(cards):
    """The sensor writes the PRs naming a card (card: <id>) as its github/card-link reading; by-source then finds it."""
    card = software(cards, 'Balance')
    pr2 = 'https://github.com/Vexa-ai/vexa-platform/pull/572'
    cards.write_reading(ORG, card['id'], 'github/card-link', {'prs': [pr2]}, {'read_at': 1_793_500_000.0})
    assert card['id'] in cards.by_source(ORG, pr2)['cards']
    assert card['id'] in cards.by_source(ORG, 'Vexa-ai/vexa-platform#572')['cards']
    assert cards.by_source(ORG, 'https://github.com/Vexa-ai/vexa-platform/pull/570')['cards'] == []
    with pytest.raises(CRMError):
        cards.write_reading(ORG, card['id'], 'github/card-link', {'prs': ['not a link']}, {'read_at': 1_793_500_001.0})


def test_a_new_card_supersedes_open_cards_and_the_rule_drops_them(cards):
    """2 Nov: re-created cards replace the old ones; Supersedes is set at creation, the rule drops each open one."""
    old = cards.create(ORG, ANA, title='Call Marvin', workspace='sales', fields={'Waiting': 'ceo'})['card']
    soft = software(cards, 'Balance', Waiting='ceo')
    done = software(cards, 'Shipped', Waiting='ceo')
    cards.set_stage(ORG, done['id'], 'In main', '#9 was merged')
    with pytest.raises(CRMError):
        cards.create(ORG, ANA, title='New', workspace='sales', fields={'Waiting': 'ceo', 'Supersedes': [done['id']]})
    new = cards.create(ORG, ANA, title='Call Marvin', workspace='sales',
                       fields={'Waiting': 'ceo', 'Supersedes': [old['id'], soft['id']]})
    # a plain card is the rule's only where the policy gives it every State (test_card_field_writers covers that)
    assert [x['status'] for x in new['superseded']] == ['refused', 'moved']
    assert cards.software_cards(ORG, record=soft['id'])['cards'][0]['state'] == 'Dropped'
    ev = cards.software_cards(ORG, record=soft['id'])['cards'][0]['stage_evidence']
    assert ev['because'] == f"superseded by {new['card']['id']}"
    # the rule's later read of its facts does not bring it back
    assert cards.set_stage(ORG, soft['id'], 'Idea', 'no pull request')['status'] == 'unchanged'
    # set at creation only
    with pytest.raises(CRMError):
        cards.update(ORG, ANA, new['card']['id'], new['card']['revision'], fields={'Supersedes': [old['id']]})


def test_another_roles_card_is_not_superseded_by_someone_else(cards):
    theirs = cards.create(ORG, ANA, title='Theirs', workspace='sales', fields={'Waiting': 'business'})['card']
    other = Caller(subject='8', email='u8@acme.test', workspaces=('sales',), writable=('sales',))
    with pytest.raises(Forbidden):
        cards.create(ORG, other, title='Mine', workspace='sales', fields={'Waiting': 'ceo', 'Supersedes': [theirs['id']]})


def test_the_same_stage_on_new_facts_rewrites_the_evidence(cards):
    """2 Nov truth check: a Review card kept «no reading yet» after its PR was read, since an unchanged stage wrote nothing."""
    card = software(cards)
    pr = [{'fact': 'pr', 'value': {'url': PR, 'state': 'open', 'head_sha': 'abc1234'}, 'source': 'github/delivery'}]
    cards.set_stage(ORG, card['id'], 'Review', '#1962 is open for review', facts=pr)
    cards.set_stage(ORG, card['id'], None, 'no reading yet for x')                # the stage kept on no reading
    got = cards.set_stage(ORG, card['id'], 'Review', '#1962 is open for review', facts=pr)
    ev = cards.software_cards(ORG, record=card['id'])['cards'][0]['stage_evidence']
    assert got['status'] == 'evidence' and ev['facts'] == pr and not ev.get('no_reading')
    assert cards.set_stage(ORG, card['id'], 'Review', '#1962 is open for review', facts=pr)['status'] == 'unchanged'
    moved = [{'fact': 'pr', 'value': {'url': PR, 'state': 'open', 'head_sha': 'def5678'}, 'source': 'github/delivery'}]
    assert cards.set_stage(ORG, card['id'], 'Review', '#1962 is open at def5678', facts=moved)['status'] == 'evidence'


def test_a_legacy_done_software_card_is_read_again_and_moved_to_in_main(cards, store):
    """Founder 2 Nov 12:5xZ: the software end stage is «In main». A card left at «Done» is outside the lifecycle, so the
    recheck reads it again and the rule moves it, with a receipt."""
    card = software(cards, 'Legacy')
    with store.begin() as c:                                              # a card written before the rename
        row = store._row(c, ORG, card['id'])
        c.execute(s.records.update().where(s.records.c.id == card['id'])
                  .values(fields={**row['fields'], 'State': 'Done'}))
    assert card['id'] in {x['id'] for x in cards.software_cards(ORG)['cards']}
    got = cards.set_stage(ORG, card['id'], 'In main', '#1962 was merged at 2026-11-01T10:00:00Z',
                          facts=[{'fact': 'pr', 'value': {'url': PR, 'state': 'merged', 'base': 'main',
                                                          'default_branch': 'main'}}], mode='recheck')
    assert (got['was'], got['stage'], got['status']) == ('Done', 'In main', 'moved')
    assert card['id'] not in {x['id'] for x in cards.software_cards(ORG)['cards']}


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
    merged = {**FACT, 'value': {'state': 'merged', 'head_sha': 'abc1234'}}
    r = client.post('/internal/cards/stage', json={**stage, 'stage': 'In main', 'mode': 'recheck', 'facts': [merged]},
                    headers=hdr)
    assert r.json()['status'] == 'corrected'
    assert 400 <= client.post('/internal/cards/stage', json={**stage, 'stage': 'Doing'}, headers=hdr).status_code < 500
    # null: the rule has no stage (no reading); the route keeps the State and says so
    r = client.post('/internal/cards/stage', json={**stage, 'stage': None, 'because': 'no reading yet for x'}, headers=hdr)
    assert r.status_code == 200 and r.json()['status'] == 'kept' and r.json()['stage'] == 'In main', r.text


# ── no reading: the State is kept; a stage set on that absence is reverted ───────────────────────
def test_no_reading_keeps_the_state_and_records_why_once(cards, store):
    card = software(cards)
    cards.set_stage(ORG, card['id'], 'Review', '#1962 is open for review at abc1234', [FACT])
    n = len(revisions(store, card['id']))
    out = cards.set_stage(ORG, card['id'], None, f'no reading yet for {PR}', [], mode='recheck')
    assert out['status'] == 'kept' and out['stage'] == 'Review'
    got = cards.read(ORG, ANA, card['id'])
    assert got['state'] == 'Review'
    ev = got['stage_evidence']
    assert ev['stage'] is None and ev['no_reading'] is True and ev['kept'] == 'Review'
    assert ev['because'] == f'no reading yet for {PR}' and 'restored' not in ev
    assert revisions(store, card['id'])[-1].reason == f'Kept Review: no reading yet for {PR}'
    assert len(revisions(store, card['id'])) == n + 1
    # the same evidence again writes nothing
    assert cards.set_stage(ORG, card['id'], None, f'no reading yet for {PR}', [], mode='recheck')['status'] == 'unchanged'
    assert len(revisions(store, card['id'])) == n + 1


def test_a_stage_the_rule_set_on_no_reading_is_reverted(cards, store):
    card = software(cards)
    cards.set_stage(ORG, card['id'], 'Review', '#1962 is open for review at abc1234', [FACT])
    # the old rule read «no reading» as «no pull request» and moved it to Idea with no fact
    cards.set_stage(ORG, card['id'], 'Idea', 'no pull request and nobody holds the card', [])
    assert cards.read(ORG, ANA, card['id'])['state'] == 'Idea'
    out = cards.set_stage(ORG, card['id'], None, f'no reading yet for {PR}', [], mode='recheck')
    assert out['status'] == 'restored' and out['stage'] == 'Review' and out['was'] == 'Idea'
    got = cards.read(ORG, ANA, card['id'])
    assert got['state'] == 'Review'
    ev = got['stage_evidence']
    assert ev['restored'].startswith('was Idea, back to Review: Idea was set with no reading')
    last = revisions(store, card['id'])[-1]
    assert last.actor_id == STAGE_RULE_ACTOR and last.reason.startswith('Restored: was Idea, back to Review')
    # once kept, a second call is unchanged and never reverts further
    assert cards.set_stage(ORG, card['id'], None, f'no reading yet for {PR}', [], mode='recheck')['status'] == 'unchanged'
    assert cards.read(ORG, ANA, card['id'])['state'] == 'Review'


def test_a_stage_set_on_a_read_fact_is_never_reverted_by_no_reading(cards):
    card = software(cards)
    cards.set_stage(ORG, card['id'], 'Review', '#1962 is open for review at abc1234', [FACT])
    closed = {**FACT, 'value': {'state': 'closed'}}
    cards.set_stage(ORG, card['id'], 'Idea', '#1962 was closed without a merge', [closed])
    out = cards.set_stage(ORG, card['id'], None, f'no reading yet for {PR}', [])
    assert out['status'] == 'kept' and cards.read(ORG, ANA, card['id'])['state'] == 'Idea'


def test_a_correction_marks_only_a_change_of_read_facts(cards, store):
    card = software(cards)
    # a stage that rested on no read fact (a hold, an absence): the first facts move it plainly
    claim = {'fact': 'claim', 'value': {'holder': 's2-worker@bbb'}, 'source': 'flows/claim', 'read_at': 1.0}
    cards.set_stage(ORG, card['id'], 'Code', 'held by s2-worker@bbb, no pull request yet', [claim])
    out = cards.set_stage(ORG, card['id'], 'Review', '#1962 is open for review at abc1234', [FACT], mode='recheck')
    assert out['status'] == 'moved'
    ev = cards.read(ORG, ANA, card['id'])['stage_evidence']
    assert 'correction' not in ev and revisions(store, card['id'])[-1].reason.startswith('Moved to Review')
    # the same read facts, another stage (the rule itself changed): a plain move, no correction
    out = cards.set_stage(ORG, card['id'], 'Code', '#1962 is a draft', [FACT], mode='recheck')
    assert out['status'] == 'moved' and 'correction' not in cards.read(ORG, ANA, card['id'])['stage_evidence']
    # the facts changed (the pull request was closed): a correction
    closed = {**FACT, 'value': {'state': 'closed', 'head_sha': 'abc1234'}}
    out = cards.set_stage(ORG, card['id'], 'Idea', '#1962 was closed without a merge', [closed], mode='recheck')
    assert out['status'] == 'corrected'
    assert cards.read(ORG, ANA, card['id'])['stage_evidence']['correction'].startswith('was Code, now Idea')


def test_reverting_a_stage_set_on_no_reading_is_not_a_correction(cards, store):
    card = software(cards)
    cards.set_stage(ORG, card['id'], 'Review', '#1962 is open for review at abc1234', [FACT])
    cards.set_stage(ORG, card['id'], 'Idea', 'no pull request and nobody holds the card', [], mode='recheck')
    out = cards.set_stage(ORG, card['id'], None, f'no reading yet for {PR}', [], mode='recheck')
    assert out['status'] == 'restored'
    ev = cards.read(ORG, ANA, card['id'])['stage_evidence']
    assert 'correction' not in ev and ev['restored'].startswith('was Idea, back to Review')
    assert not revisions(store, card['id'])[-1].reason.startswith('Corrected')
