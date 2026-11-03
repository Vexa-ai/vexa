"""A CROSS-ORG ANSWER CARRIES AN EXPLICIT LIST OF KEYS, AND EVERY OTHER KEY IS BLANK.

`test_cross_org_audience.py` pins the record's fields. This file pins the whole answer around them:
a read, a history, a search, a card and a card list answered to a room with people from outside
the org carry exactly the keys below, and the org's own facts about the record (where it came from,
its identifiers, the workspaces and pages around it, pending proposals, revision reasons, card
movement, members and boards) are blank. A key added later is blank too unless it is put on the
list (`store.CROSS_ORG_KEYS`).
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.bootstrap import bootstrap_org
from crm.caller import Caller, from_validate
from crm.card_ops import Cards
from crm.store import Forbidden, Store

SCHEMA = json.loads((Path(__file__).parents[1] / 'contracts/crm.v1/crm.schema.json').read_text())
ORG = 'one'
ADMIN = Caller(subject='1', email='admin@acme.test', is_admin=True)
PERSON = Caller(subject='2', email='u2@acme.test', workspaces=('team',), writable=('team',))
PRIVATE = ('Negotiation', 'p-7', 'notes/globex.md', 'domain:', 'Signed the NDA', 'crm-import',
           'Call the CFO', 'Legal review', 'Because the CFO asked', 'Bump the stage')

# D4: a room (shared caller) never reads match_ids or account_id: they are absent, not blanked.
RECORD_KEYS = {'id', 'org_id', 'object_type', 'revision', 'quarantined', 'fields', 'narrative', 'audience',
               'card', 'href', 'links', 'actions', 'sources', 'mentioned_in', 'bound_to', 'proposals'}
SNAPSHOT_KEYS = {'id', 'org_id', 'object_type', 'revision', 'quarantined', 'fields', 'narrative', 'audience'}


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


def _turn(cross_org=True) -> Caller:
    body = {'user_id': 2, 'email': 'u2@acme.test', 'workspaces': ['team'], 'writable_workspaces': ['team'],
            'delegation': {'regime': 'human', 'workspaces': ['team'], 'shared': True, 'unit': 'room-1',
                           **({'audience': 'cross_org'} if cross_org else {})}}
    return from_validate('vxd_x.y.z', body)


def _company(store):
    rid = store.create(ORG, ADMIN, 'Company', {'Name': 'Globex', 'Domain': 'globex.test', 'Stage': 'Negotiation',
                                               'Owner': 'p-7'}, narrative='Signed the NDA', reason='Signed the NDA',
                       evidence=[{'meeting': 'Signed the NDA'}], idempotency_key='co-globex',
                       match=['domain:globex.test'])['record_id']
    store.apply_workspace_snapshot(ORG, 'team', 'c1', '2026-10-16T10:00:00Z',
                                   [{'page': 'notes/globex.md', 'record': rid}],
                                   [{'subject': '2', 'role': 'contributor'}])
    store.bind(ORG, ADMIN, 'team', rid)
    with store.engine.begin() as c:
        c.execute(s.source_mappings.insert().values(org_id=ORG, system='crm-import', source_org='src',
                                                    object_type='Company', source_id='001', record_id=rid))
    store.change(ORG, ADMIN, rid, 1, {'Stage': 'Negotiation'}, reason='Bump the stage',
                 evidence=[{'page': 'Bump the stage'}])
    # a pending proposal the room's own subject wrote, with a private reason and evidence
    store.change(ORG, _turn(cross_org=False), rid, 2, {'Stage': 'Won'}, reason='Bump the stage',
                 evidence=[{'page': 'Bump the stage'}], propose=True)
    store.configure(ORG, ADMIN, 'Company', {'title_field': 'Name', 'sections': [
        {'title': 'Deal', 'fields': [{'field': 'Stage'}, {'field': 'Owner'}]},
        {'title': 'Who', 'fields': [{'field': 'Name'}, {'field': 'Domain'}]}]}, 0, 'layout')
    return rid


def conforms(shape, data):
    errors = list(Draft202012Validator({'$ref': '#/$defs/' + shape, '$defs': SCHEMA['$defs']}).iter_errors(data))
    assert not errors, errors[0].message


def _no_private(answer):
    text = json.dumps(answer)
    for value in PRIVATE:
        assert value not in text, value


def test_a_cross_org_read_carries_exactly_the_listed_keys_and_blank_org_facts(store):
    rid = _company(store)
    rec = store.get(ORG, _turn(), rid)
    assert set(rec) == RECORD_KEYS
    assert rec['fields'] == {'Name': 'Globex', 'Domain': 'globex.test'}
    assert rec['narrative'] is None and rec['audience'] == 'cross_org'
    assert rec['sources'] == [] and 'match_ids' not in rec and 'account_id' not in rec
    assert rec['mentioned_in'] == [] and rec['bound_to'] == [] and rec['proposals'] == []
    assert rec['card']['layout']['sections'] == [{'title': 'Who', 'fields': [
        {'field': 'Name', 'label': '', 'format': 'text', 'currency': 'USD'},
        {'field': 'Domain', 'label': '', 'format': 'text', 'currency': 'USD'}]}]
    assert all(set(link) <= {'field', 'record_id', 'object_type', 'label'} for link in rec['links'])
    _no_private(rec)
    conforms('Record', rec)


def test_the_same_read_in_the_orgs_own_room_has_the_members_fields_and_no_free_text(store):
    # D4: the org's own room reads every field its members all read (here: all of them), never the
    # email-derived identifiers, the narrative, or a proposal's free-text reason and evidence.
    rid = _company(store)
    rec = store.get(ORG, _turn(cross_org=False), rid)
    assert rec['fields']['Stage'] == 'Negotiation' and rec['sources'] and 'match_ids' not in rec
    assert rec['bound_to'] == ['team'] and rec['mentioned_in'] and rec['proposals']
    assert rec['narrative'] is None and rec['proposals'][0]['reason'] is None and 'audience' not in rec
    # the person in their own (non-shared) read still sees all of it
    assert store.get(ORG, ADMIN, rid)['proposals'][0]['reason'] == 'Bump the stage'


def test_a_cross_org_history_carries_no_reason_or_evidence(store):
    rid = _company(store)
    revisions = store.history(ORG, _turn(), rid)['revisions']
    assert len(revisions) == 2
    for rev in revisions:
        assert set(rev) == {'revision', 'created_at', 'before', 'after', 'reason', 'evidence'}
        assert rev['reason'] is None and rev['evidence'] == []
        assert set(rev['after']) == SNAPSHOT_KEYS and rev['after']['fields'] == {'Name': 'Globex',
                                                                                   'Domain': 'globex.test'}
    _no_private(revisions)
    own = store.history(ORG, _turn(cross_org=False), rid)['revisions']
    assert own[0]['reason'] is None                                    # D4: no free text in any room
    assert store.history(ORG, ADMIN, rid)['revisions'][0]['reason'] == 'Bump the stage'


def test_a_cross_org_search_may_filter_on_public_fields_only(store):
    _company(store)
    with pytest.raises(Forbidden, match='Filter field unavailable'):
        store.search(ORG, _turn(), 'Company', {'Stage': 'Negotiation'})
    with pytest.raises(Forbidden, match='Filter field unavailable'):
        store.search(ORG, _turn(), 'Company', {'Owner': 'p-7'})
    assert len(store.search(ORG, _turn(), 'Company', {'Domain': 'globex.test'})['records']) == 1
    # the org's own room filters on any readable field, as before
    assert len(store.search(ORG, _turn(cross_org=False), 'Company', {'Stage': 'Negotiation'})['records']) == 1


def test_a_cross_org_search_answer_carries_exactly_the_listed_keys(store):
    _company(store)
    out = store.search(ORG, _turn(), 'Company', {'Name': 'Globex'})
    assert set(out) == {'records', 'next_offset', 'card', 'href'}
    (rec,) = out['records']
    assert set(rec) <= SNAPSHOT_KEYS | {'href', 'links'} and SNAPSHOT_KEYS | {'href'} <= set(rec)
    assert rec['fields'] == {'Name': 'Globex', 'Domain': 'globex.test'}
    assert [sec['title'] for sec in out['card']['layout']['sections']] == ['Who']
    _no_private(out)


# ── cards ───────────────────────────────────────────────────────────────────────────────────────
CARD_KEYS = {'id', 'ref', 'type', 'revision', 'title', 'about', 'state', 'states', 'side_states', 'next',
             'waiting', 'summary', 'due', 'progress', 'workspaces', 'parent', 'children', 'members', 'face',
             'movement', 'stalled', 'can', 'href', 'new',
             # D11–D14 coordination: present, and blank for a cross-org room (no next actor, no
             # requester, no decision records, no viewer actions)
             'kind', 'next_actor', 'requester', 'next_check_at', 'chain', 'origin', 'open', 'handoff', 'gates',
             'decisions', 'last_decision', 'viewer'}


def _card(store):
    cards = Cards(store)
    # the room's roster (policy/members.json, carried by the snapshot): D4 reads nothing without one
    store.apply_workspace_snapshot(ORG, 'team', 'c0', '2026-10-16T09:00:00Z', [],
                                   [{'subject': '2', 'role': 'contributor'}])
    made = cards.create(ORG, PERSON, 'Globex rollout', workspace='team', about='workspace:team',
                        fields={'Summary': 'Signed the NDA'})['card']
    cards.update(ORG, PERSON, made['id'], 1, fields={'Next': 'Call the CFO', 'Waiting': 'Legal review'})
    cards.update(ORG, PERSON, made['id'], 2, transition='Doing')
    cards.propose(ORG, _turn(cross_org=False), 'Another card', workspace='team', reason='Because the CFO asked')
    return cards, made['id']


def test_a_cross_org_card_read_carries_no_movement_activity_members_or_boards(store):
    cards, cid = _card(store)
    card = cards.read(ORG, _turn(), cid)
    assert set(card) == CARD_KEYS | {'activity'}
    assert card['title'] == 'Globex rollout'
    assert card['activity'] == [] and card['movement'] is None and card['stalled'] is False
    assert card['members'] == [] and card['workspaces'] == []
    assert card['next'] is None and card['waiting'] is None and card['summary'] is None
    assert card['state'] is None and card['about'] is None
    assert card['next_actor'] is None and card['requester'] is None and card['decisions'] == []
    assert card['viewer'] == {'role': 'watcher', 'seen': False, 'actions': []}
    _no_private(card)
    conforms('CardView', card)
    # the org's own room reads the card in full
    own = cards.read(ORG, _turn(cross_org=False), cid)
    assert own['activity'] and own['members'] and own['workspaces'] == ['team'] and own['next'] == 'Call the CFO'


def test_a_cross_org_card_list_carries_projected_cards_and_no_proposals(store):
    cards, cid = _card(store)
    out = cards.list(ORG, _turn())
    assert set(out) == {'cards', 'proposals', 'counts'}
    assert out['proposals'] == []
    (card,) = out['cards']
    assert set(card) == CARD_KEYS and card['id'] == cid
    assert card['movement'] is None and card['members'] == [] and card['workspaces'] == []
    _no_private(out)
    conforms('CardList', out)
    own = cards.list(ORG, _turn(cross_org=False))
    assert own['proposals'] and own['cards'][0]['movement']
