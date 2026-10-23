"""The desk (desktop phase D1): one person's placements, arrangement versions and desk proposals.

The person, or their agent acting on the person's own request in this turn, arranges the desk
directly; anything unprompted proposes. Every applied change is a new append-only version, so Undo
restores the earlier layout AND placements. Layout never touches a record.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.pool import StaticPool

from crm import migrate, schema as s
from crm.caller import Caller
from crm.desk import Desk, DEFAULT_SECTIONS, PLACEMENTS_MAX, PENDING_MAX, PER_SESSION_PER_DAY
from crm.store import CRMError, Conflict, NotFound, Refused, Store

ORG = 'one'
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
def engine():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    return engine


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def desk(engine, clock):
    return Desk(Store(engine), clock=clock)


ME = Caller(subject='7', workspaces=('sales', 'ops'))
OTHER = Caller(subject='8', workspaces=('sales',))


def agent(regime='human', shared=False, session='pchat-1', workspaces=('sales',)):
    return Caller(subject='7', workspaces=workspaces, delegated=True, regime=regime, shared=shared,
                  session=session, trace='t-' + session)


def arrange(desk, caller, ops, base=None, org=ORG, name=None):
    if base is None:
        base = desk.read(org, caller)['version']
    return desk.arrange(org, caller, ops, base, name=name)


# ── read: the default ─────────────────────────────────────────────────────────────────────────
def test_an_empty_desk_reads_the_default_layout(desk):
    d = desk.read(ORG, ME)
    assert d['version'] == 0 and d['undo_version'] is None
    assert [x['id'] for x in d['layout']['sections']] == ['workspaces', 'agents', 'files', 'cards'] == list(DEFAULT_SECTIONS)
    assert all(x == {'id': x['id'], 'hidden': False, 'collapsed': False, 'column': 1, 'span': 1, 'size': 'normal'}
               for x in d['layout']['sections'])
    assert d['layout']['order'] == {} and d['layout']['groups'] == {} and d['layout']['filters'] == {}
    assert d['layout']['columns'] == {'phone': 1, 'desktop': 1}
    assert d['placements'] == [] and d['proposals'] == [] and d['saved'] == []
    conforms('Desk', d, 'empty')


# ── each op ─────────────────────────────────────────────────────────────────────────────────────
def sections(desk, caller=ME):
    return desk.read(ORG, caller)['layout']['sections']


def ids(desk, caller=ME):
    return [x['id'] for x in sections(desk, caller)]


def section(desk, sid, caller=ME):
    return next(x for x in sections(desk, caller) if x['id'] == sid)


def test_section_ops_and_their_one_line_summaries(desk):
    r = arrange(desk, ME, [{'op': 'section_top', 'section': 'agents'}])
    assert r == {'status': 'applied', 'version': 1, 'undo_version': 0, 'summary': 'Moved Agents above Workspaces'}
    conforms('ArrangeResult', r, 'applied')
    assert ids(desk) == ['agents', 'workspaces', 'files', 'cards']
    assert arrange(desk, ME, [{'op': 'hide', 'section': 'files'}])['summary'] == 'Hid Files'
    assert section(desk, 'files')['hidden'] is True
    assert arrange(desk, ME, [{'op': 'show', 'section': 'files'}])['summary'] == 'Showed Files'
    assert arrange(desk, ME, [{'op': 'collapse', 'section': 'cards'}])['summary'] == 'Collapsed Cards'
    assert section(desk, 'cards')['collapsed'] is True
    assert arrange(desk, ME, [{'op': 'expand', 'section': 'cards'}])['summary'] == 'Expanded Cards'
    r = arrange(desk, ME, [{'op': 'section_order', 'sections': ['files', 'workspaces']}])
    assert ids(desk) == ['files', 'workspaces', 'agents', 'cards']
    assert r['summary'] == 'Reordered the sections'
    arrange(desk, ME, [{'op': 'section_place', 'section': 'agents', 'column': 2, 'span': 1, 'side_by_side_with': 'files'}])
    assert ids(desk)[:2] == ['files', 'agents'] and section(desk, 'agents')['column'] == 2
    assert arrange(desk, ME, [{'op': 'section_size', 'section': 'files', 'size': 'tall'}])['summary'] == 'Made Files tall'
    assert section(desk, 'files')['size'] == 'tall'


def test_item_ops_order_move_group_filter(desk):
    arrange(desk, ME, [{'op': 'order', 'section': 'workspaces', 'ids': ['workspace:ops', 'workspace:sales']}])
    assert desk.read(ORG, ME)['layout']['order']['workspaces'] == ['workspace:ops', 'workspace:sales']
    r = arrange(desk, ME, [{'op': 'move', 'id': 'workspace:sales', 'before': 'workspace:ops'}])
    assert desk.read(ORG, ME)['layout']['order']['workspaces'] == ['workspace:sales', 'workspace:ops']
    assert r['summary'] == 'Reordered Workspaces'
    arrange(desk, ME, [{'op': 'move', 'id': 'workspace:ops', 'after': 'workspace:new'}])
    assert desk.read(ORG, ME)['layout']['order']['workspaces'] == ['workspace:sales', 'workspace:new', 'workspace:ops']
    # a section moved with move is the section order
    arrange(desk, ME, [{'op': 'move', 'id': 'cards', 'before': 'workspaces'}])
    assert ids(desk)[0] == 'cards'
    assert arrange(desk, ME, [{'op': 'group', 'section': 'files', 'by': 'workspace'}])['summary'] == 'Grouped Files by workspace'
    assert desk.read(ORG, ME)['layout']['groups']['files'] == {'by': 'workspace'}
    arrange(desk, ME, [{'op': 'group', 'section': 'agents', 'named': [{'title': 'Daily', 'ids': ['agent:r1']}]}])
    assert desk.read(ORG, ME)['layout']['groups']['agents'] == {'named': [{'title': 'Daily', 'ids': ['agent:r1']}]}
    r = arrange(desk, ME, [{'op': 'filter', 'section': 'cards', 'where': 'about.company == "c1"', 'until': '2026-10-22T00:00:00Z'}])
    assert r['summary'] == 'Filtered Cards'
    assert desk.read(ORG, ME)['layout']['filters']['cards'] == {'where': 'about.company == "c1"', 'until': '2026-10-22T00:00:00Z'}
    assert arrange(desk, ME, [{'op': 'filter', 'section': 'cards', 'where': None}])['summary'] == 'Cleared the Cards filter'
    assert 'cards' not in desk.read(ORG, ME)['layout']['filters']
    conforms('DeskLayout', desk.read(ORG, ME)['layout'], 'arranged')


@pytest.mark.parametrize('op', [
    {'op': 'explode'}, {'op': 'hide'}, {'op': 'hide', 'section': 'people'},
    {'op': 'move', 'id': 'workspace:a'}, {'op': 'move', 'id': 'workspace:a', 'before': 'b', 'after': 'c'},
    {'op': 'move', 'id': 'workspace:a', 'before': 'agent:b'},
    {'op': 'group', 'section': 'files', 'by': 'colour'}, {'op': 'group', 'section': 'files'},
    {'op': 'section_order', 'sections': ['files', 'files']},
    {'op': 'section_place', 'section': 'files', 'column': 4},
    {'op': 'section_size', 'section': 'files', 'size': 'huge'},
    {'op': 'filter', 'section': 'cards', 'where': 'x' * 301},
    {'op': 'filter', 'section': 'cards', 'where': 'a == 1', 'until': 'tomorrow'},
    {'op': 'hide', 'section': 'files', 'extra': 1},
    {'op': 'place', 'ref': 'workspace:sales'}, {'op': 'place', 'ref': 'file:sales/../etc/passwd'},
    {'op': 'place', 'ref': 'nonsense'}, {'op': 'save', 'name': ''}, {'op': 'restore', 'name': 'never saved'},
])
def test_a_bad_op_is_refused_naming_its_index(desk, op):
    with pytest.raises(CRMError) as e:
        arrange(desk, ME, [{'op': 'hide', 'section': 'cards'}, op])
    assert e.value.status == 422 and 'ops[1]' in str(e.value)
    assert desk.read(ORG, ME)['version'] == 0           # nothing of a refused list is applied


def test_the_op_list_is_one_to_fifty(desk):
    for ops in ([], [{'op': 'hide', 'section': 'cards'}] * 51):
        with pytest.raises(CRMError) as e:
            arrange(desk, ME, ops)
        assert e.value.status == 422


# ── place / remove ───────────────────────────────────────────────────────────────────────────────
def test_place_and_remove_agents_and_readable_files(desk, clock):
    r = arrange(desk, ME, [{'op': 'place', 'ref': 'file:sales/notes/pricing-notes.md'}])
    assert r['summary'] == 'Placed Pricing notes'
    arrange(desk, ME, [{'op': 'place', 'ref': 'agent:r1'}, {'op': 'place', 'ref': 'file:7/todo.md'}])   # own desk slug
    refs = [p['ref'] for p in desk.read(ORG, ME)['placements']]
    assert refs == ['file:sales/notes/pricing-notes.md', 'agent:r1', 'file:7/todo.md']
    p = desk.read(ORG, ME)['placements'][0]
    assert p['placed_at'] == clock.at.isoformat() and p['seen_at'] is None
    assert arrange(desk, ME, [{'op': 'remove', 'ref': 'agent:r1'}])['summary'] == 'Removed an agent'
    assert [p['ref'] for p in desk.read(ORG, ME)['placements']] == ['file:sales/notes/pricing-notes.md', 'file:7/todo.md']
    # placing what is already placed keeps its first placed_at
    clock.at += timedelta(hours=1)
    arrange(desk, ME, [{'op': 'place', 'ref': 'file:7/todo.md'}])
    assert desk.read(ORG, ME)['placements'][1]['placed_at'] == (clock.at - timedelta(hours=1)).isoformat()
    # placements are per person
    assert desk.read(ORG, OTHER)['placements'] == []


def test_a_file_outside_the_callers_workspaces_is_not_readable(desk):
    with pytest.raises(Refused) as e:
        arrange(desk, ME, [{'op': 'place', 'ref': 'file:finance/plan.md'}])
    assert e.value.status == 403 and e.value.body['reason'] == 'not_readable'
    conforms('Refusal', e.value.body, 'not-readable')
    with pytest.raises(Refused):
        arrange(desk, OTHER, [{'op': 'place', 'ref': 'file:7/todo.md'}])        # someone else's desk


def test_a_card_ref_is_not_enabled_in_d1(desk):
    with pytest.raises(Refused) as e:
        arrange(desk, ME, [{'op': 'place', 'ref': 'card:r-1'}])
    assert e.value.status == 422 and e.value.body['reason'] == 'ref_kind_not_enabled'
    conforms('Refusal', e.value.body, 'ref-kind-not-enabled')


def test_placements_are_capped(desk):
    arrange(desk, ME, [{'op': 'place', 'ref': f'agent:r{i}'} for i in range(PLACEMENTS_MAX)])
    with pytest.raises(Refused) as e:
        arrange(desk, ME, [{'op': 'place', 'ref': 'agent:one-more'}])
    assert e.value.status == 409 and e.value.body['reason'] == 'desk_placements_max'
    arrange(desk, ME, [{'op': 'remove', 'ref': 'agent:r0'}, {'op': 'place', 'ref': 'agent:one-more'}])
    assert len(desk.read(ORG, ME)['placements']) == PLACEMENTS_MAX


# ── who may arrange directly ─────────────────────────────────────────────────────────────────────
def test_the_person_and_their_agent_in_their_own_chat_arrange_directly(desk):
    r = arrange(desk, ME, [{'op': 'hide', 'section': 'files'}])
    assert r['status'] == 'applied'
    r = arrange(desk, agent(), [{'op': 'show', 'section': 'files'}])
    assert r['status'] == 'applied' and r['version'] == 2
    with desk.store.engine.connect() as c:
        rows = c.execute(select(s.desk_layouts).order_by(s.desk_layouts.c.version)).mappings().all()
    assert [(x['author'], x['trace']) for x in rows] == [('person', ''), ('agent:pchat-1', 't-pchat-1')]


@pytest.mark.parametrize('worker', [agent(regime='autonomous'), agent(regime='scheduled'), agent(regime=''),
                                    agent(shared=True)])
def test_anything_unprompted_or_shared_is_told_to_propose(desk, worker):
    with pytest.raises(Refused) as e:
        arrange(desk, worker, [{'op': 'hide', 'section': 'files'}])
    assert e.value.status == 403 and e.value.body['reason'] == 'propose_instead'
    assert 'desktop_arrange_propose' in e.value.body['instruction']
    conforms('Refusal', e.value.body, 'propose-instead')
    with pytest.raises(Refused):
        desk.undo(ORG, worker, 0, 0)
    assert desk.read(ORG, ME)['version'] == 0


def test_a_stale_base_version_is_desk_changed(desk):
    arrange(desk, ME, [{'op': 'hide', 'section': 'files'}])
    with pytest.raises(Refused) as e:
        desk.arrange(ORG, ME, [{'op': 'hide', 'section': 'cards'}], 0)
    assert e.value.status == 409 and e.value.body == {**e.value.body, 'reason': 'desk_changed', 'version': 1}
    conforms('Refusal', e.value.body, 'desk-changed')


# ── undo and named arrangements ──────────────────────────────────────────────────────────────────
def test_undo_restores_the_layout_and_the_placements(desk):
    arrange(desk, ME, [{'op': 'place', 'ref': 'agent:r1'}])                                   # v1
    r = arrange(desk, ME, [{'op': 'hide', 'section': 'agents'}, {'op': 'remove', 'ref': 'agent:r1'},
                           {'op': 'place', 'ref': 'file:sales/a.md'}])                         # v2
    assert r['undo_version'] == 1
    u = desk.undo(ORG, ME, 1, 2)
    assert u['status'] == 'applied' and u['version'] == 3 and u['undo_version'] == 2
    d = desk.read(ORG, ME)
    assert section(desk, 'agents')['hidden'] is False
    assert [p['ref'] for p in d['placements']] == ['agent:r1']
    desk.undo(ORG, ME, 0, 3)                                                                   # back to the default
    assert desk.read(ORG, ME)['placements'] == [] and desk.read(ORG, ME)['version'] == 4
    with pytest.raises(NotFound):
        desk.undo(ORG, ME, 9, 4)
    with pytest.raises(Refused):
        desk.undo(ORG, ME, 1, 2)                                                               # stale base


def test_save_and_restore_a_named_arrangement(desk):
    arrange(desk, ME, [{'op': 'place', 'ref': 'agent:r1'}, {'op': 'section_top', 'section': 'files'}],
            name='Monday review')
    arrange(desk, ME, [{'op': 'remove', 'ref': 'agent:r1'}, {'op': 'section_top', 'section': 'workspaces'},
                       {'op': 'save', 'name': 'Focus'}])
    assert desk.read(ORG, ME)['saved'] == ['Focus', 'Monday review']
    r = arrange(desk, ME, [{'op': 'restore', 'name': 'Monday review'}])
    assert r['summary'] == 'Restored "Monday review"'
    assert ids(desk)[0] == 'files' and [p['ref'] for p in desk.read(ORG, ME)['placements']] == ['agent:r1']


# ── proposals ────────────────────────────────────────────────────────────────────────────────────
def test_any_agent_proposes_and_the_person_applies(desk):
    worker = agent(regime='scheduled', session='flow-9')
    p = desk.propose(ORG, worker, [{'op': 'group', 'section': 'files', 'by': 'workspace'}], 'You have many files')
    assert p['status'] == 'proposed' and p['proposal_id']
    d = desk.read(ORG, ME)
    assert d['version'] == 0
    [shown] = d['proposals']
    assert shown['id'] == p['proposal_id'] and shown['author'] == 'agent:flow-9'
    assert shown['summary'] == 'Grouped Files by workspace' and shown['reason'] == 'You have many files'
    conforms('Desk', d, 'with-proposal')
    conforms('DeskProposal', shown, 'pending')
    # a delegated caller never applies a proposal, even with the person in the loop
    with pytest.raises(Refused) as e:
        desk.review(ORG, agent(), p['proposal_id'], True, 0)
    assert e.value.body['reason'] == 'person_confirmation_required'
    r = desk.review(ORG, ME, p['proposal_id'], True, 0)
    assert r['status'] == 'applied' and r['version'] == 1
    assert desk.read(ORG, ME)['layout']['groups']['files'] == {'by': 'workspace'}
    assert desk.read(ORG, ME)['proposals'] == []
    with pytest.raises(Conflict):
        desk.review(ORG, ME, p['proposal_id'], True, 1)


def test_a_dismissed_proposal_changes_nothing(desk):
    p = desk.propose(ORG, agent(shared=True), [{'op': 'hide', 'section': 'files'}], 'tidy')
    assert desk.review(ORG, ME, p['proposal_id'], False, 0) == {'status': 'dismissed'}
    assert desk.read(ORG, ME)['version'] == 0 and desk.read(ORG, ME)['proposals'] == []


def test_another_persons_proposal_is_not_found(desk):
    p = desk.propose(ORG, ME, [{'op': 'hide', 'section': 'files'}], 'mine')
    with pytest.raises(NotFound):
        desk.review(ORG, OTHER, p['proposal_id'], True, 0)


def test_proposal_ops_are_validated_and_files_must_be_readable(desk):
    with pytest.raises(CRMError) as e:
        desk.propose(ORG, agent(), [{'op': 'explode'}], 'x')
    assert e.value.status == 422
    with pytest.raises(Refused) as e:
        desk.propose(ORG, agent(), [{'op': 'place', 'ref': 'file:finance/x.md'}], 'x')
    assert e.value.body['reason'] == 'not_readable'


def test_pending_proposals_are_capped_per_person_and_per_session_per_day(desk, clock):
    for i in range(PENDING_MAX):
        desk.propose(ORG, agent(session=f's{i}'), [{'op': 'hide', 'section': 'files'}], 'x')
    with pytest.raises(Refused) as e:
        desk.propose(ORG, agent(session='s-new'), [{'op': 'hide', 'section': 'files'}], 'x')
    assert e.value.status == 409 and e.value.body['reason'] == 'proposals_pending_max'
    conforms('Refusal', e.value.body, 'proposals-pending-max')
    # one session's daily budget counts dismissed ones too
    for p in desk.read(ORG, ME)['proposals']:
        desk.review(ORG, ME, p['id'], False, 0)
    busy = agent(session='busy')
    for i in range(PER_SESSION_PER_DAY):
        p = desk.propose(ORG, busy, [{'op': 'hide', 'section': 'files'}], 'x')
        desk.review(ORG, ME, p['proposal_id'], False, 0)
    with pytest.raises(Refused) as e:
        desk.propose(ORG, busy, [{'op': 'hide', 'section': 'files'}], 'x')
    assert e.value.status == 409 and e.value.body['reason'] == 'proposals_daily_max'
    clock.at += timedelta(days=1, minutes=1)
    assert desk.propose(ORG, busy, [{'op': 'hide', 'section': 'files'}], 'x')['status'] == 'proposed'


def test_a_proposal_expires_after_seven_days(desk, clock):
    p = desk.propose(ORG, agent(), [{'op': 'hide', 'section': 'files'}], 'x')
    clock.at += timedelta(days=7, minutes=1)
    assert desk.read(ORG, ME)['proposals'] == []
    with pytest.raises(NotFound):
        desk.review(ORG, ME, p['proposal_id'], True, 0)
    # an expired proposal no longer counts against the pending cap
    for i in range(PENDING_MAX):
        desk.propose(ORG, agent(session=f'n{i}'), [{'op': 'hide', 'section': 'files'}], 'x')


# ── org isolation ────────────────────────────────────────────────────────────────────────────────
def test_each_org_has_its_own_desk(desk):
    arrange(desk, ME, [{'op': 'place', 'ref': 'agent:r1'}], org='one')
    p = desk.propose('one', agent(), [{'op': 'hide', 'section': 'files'}], 'x')
    other = desk.read('two', ME)
    assert other['version'] == 0 and other['placements'] == [] and other['proposals'] == []
    with pytest.raises(NotFound):
        desk.review('two', ME, p['proposal_id'], True, 0)
    assert arrange(desk, ME, [{'op': 'hide', 'section': 'files'}], org='two')['version'] == 1


# ── storage ──────────────────────────────────────────────────────────────────────────────────────
def test_layout_versions_are_append_only_and_the_runtime_role_cannot_rewrite_them():
    assert 'crm_desk_layouts' in migrate.APPEND_ONLY
    assert migrate.WRITES['crm_desk_layouts'] == 'INSERT'
    assert migrate.WRITES['crm_desk'] == 'INSERT, UPDATE, DELETE'
    assert migrate.WRITES['crm_desk_proposals'] == 'INSERT, UPDATE'
    assert s.SCHEMA_VERSION >= 3          # 4: card members (desktop D2)


def test_an_existing_v2_database_upgrades_to_v3():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    v3 = {'crm_desk', 'crm_desk_layouts', 'crm_desk_proposals'}
    with engine.begin() as c:
        for table in s.metadata.sorted_tables:
            if table.name not in v3:
                table.create(c)
        c.execute(text('INSERT INTO crm_schema (version) VALUES (2)'))
    assert not v3 & set(inspect(engine).get_table_names())
    with engine.begin() as c:
        migrate.tables_and_version(c)
    assert v3 <= set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert c.execute(text('SELECT version FROM crm_schema')).scalar_one() == s.SCHEMA_VERSION
        assert c.execute(text('SELECT count(*) FROM crm_schema')).scalar_one() == 1


# ── company policy narrows the limits (`_global/POLICIES.md`, crm.policies) ─────────────────────
def narrowed(engine, clock, **limits):
    from crm.policies import DEFAULTS
    current = dict(DEFAULTS, **limits)
    return Desk(Store(engine), clock=clock, limits=lambda: current), current


def test_a_narrowed_placement_cap_is_applied_and_named(engine, clock):
    desk, _ = narrowed(engine, clock, desk_placements_max=3)
    arrange(desk, ME, [{'op': 'place', 'ref': f'agent:r{i}'} for i in range(3)])
    with pytest.raises(Refused) as e:
        arrange(desk, ME, [{'op': 'place', 'ref': 'agent:one-more'}])
    assert e.value.status == 409 and e.value.body['reason'] == 'desk_placements_max'
    assert 'at most 3 ' in e.value.body['instruction']
    conforms('Refusal', e.value.body)


def test_narrowed_proposal_caps_are_applied(engine, clock):
    desk, _ = narrowed(engine, clock, card_proposals_pending_max=2, card_proposals_per_agent_per_day=3)
    for i in range(2):
        desk.propose(ORG, agent(session=f's{i}'), [{'op': 'hide', 'section': 'files'}], 'x')
    with pytest.raises(Refused) as e:
        desk.propose(ORG, agent(session='s-new'), [{'op': 'hide', 'section': 'files'}], 'x')
    assert e.value.body['reason'] == 'proposals_pending_max' and 'already has 2 ' in e.value.body['instruction']
    for p in desk.read(ORG, ME)['proposals']:
        desk.review(ORG, ME, p['id'], False, 0)
    busy = agent(session='busy')
    for i in range(3):
        p = desk.propose(ORG, busy, [{'op': 'hide', 'section': 'files'}], 'x')
        desk.review(ORG, ME, p['proposal_id'], False, 0)
    with pytest.raises(Refused) as e:
        desk.propose(ORG, busy, [{'op': 'hide', 'section': 'files'}], 'x')
    assert e.value.body['reason'] == 'proposals_daily_max' and 'its 3 ' in e.value.body['instruction']


def test_a_narrowed_ttl_expires_proposals_sooner(engine, clock):
    desk, _ = narrowed(engine, clock, card_proposal_ttl_days=1)
    p = desk.propose(ORG, agent(), [{'op': 'hide', 'section': 'files'}], 'x')
    clock.at += timedelta(days=1, minutes=1)
    assert desk.read(ORG, ME)['proposals'] == []
    with pytest.raises(NotFound):
        desk.review(ORG, ME, p['proposal_id'], True, 0)


def test_the_limits_are_read_on_every_call(engine, clock):
    desk, current = narrowed(engine, clock, desk_placements_max=1)
    arrange(desk, ME, [{'op': 'place', 'ref': 'agent:r0'}])
    with pytest.raises(Refused):
        arrange(desk, ME, [{'op': 'place', 'ref': 'agent:r1'}])
    current['desk_placements_max'] = 2
    assert arrange(desk, ME, [{'op': 'place', 'ref': 'agent:r1'}])['status'] == 'applied'


def test_limits_that_cannot_be_read_are_the_defaults(engine, clock):
    def broken():
        raise RuntimeError('policy source down')
    desk = Desk(Store(engine), clock=clock, limits=broken)
    arrange(desk, ME, [{'op': 'place', 'ref': f'agent:r{i}'} for i in range(PLACEMENTS_MAX)])
    with pytest.raises(Refused) as e:
        arrange(desk, ME, [{'op': 'place', 'ref': 'agent:one-more'}])
    assert e.value.body['reason'] == 'desk_placements_max'


def test_a_desk_above_a_newly_narrowed_cap_can_still_be_arranged_and_thinned(engine, clock):
    desk, current = narrowed(engine, clock)
    arrange(desk, ME, [{'op': 'place', 'ref': f'agent:r{i}'} for i in range(4)])
    current['desk_placements_max'] = 2
    assert arrange(desk, ME, [{'op': 'hide', 'section': 'files'}])['status'] == 'applied'
    assert arrange(desk, ME, [{'op': 'remove', 'ref': 'agent:r0'}])['status'] == 'applied'
    with pytest.raises(Refused):
        arrange(desk, ME, [{'op': 'place', 'ref': 'agent:r9'}])


# ── a chat is placeable (founder 2026-10-15: "selected agents" are chats as well as routines) ──
def test_a_chat_is_placed_like_an_agent_and_lives_in_agents(desk):
    r = arrange(desk, ME, [{'op': 'place', 'ref': 'chat:pchat-a1'}, {'op': 'place', 'ref': 'agent:r1'}])
    assert r['summary'].startswith('Placed a chat')
    arrange(desk, ME, [{'op': 'move', 'id': 'agent:r1', 'before': 'chat:pchat-a1'}])
    d = desk.read(ORG, ME)
    assert [p['ref'] for p in d['placements']] == ['chat:pchat-a1', 'agent:r1']
    assert d['layout']['order']['agents'] == ['agent:r1', 'chat:pchat-a1']
    conforms('Desk', d)
    assert arrange(desk, ME, [{'op': 'remove', 'ref': 'chat:pchat-a1'}])['summary'] == 'Removed a chat'
    assert desk.read(ORG, ME)['layout']['order']['agents'] == ['agent:r1']


def test_a_malformed_chat_ref_is_refused(desk):
    with pytest.raises(CRMError) as e:
        arrange(desk, ME, [{'op': 'place', 'ref': 'chat:'}])
    assert e.value.status == 422
