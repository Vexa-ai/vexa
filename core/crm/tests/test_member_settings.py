"""A person's settings for a workspace, on the CRM `member_of` relation (crm_member_settings).

The person is always the caller's subject. Only the person, or their agent in a turn they typed in
their own one-person chat, writes them; a read answers only the caller's own rows. Settings carry no
access: writing one for a slug grants nothing and reveals nothing.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from crm import migrate, schema as s
from crm.caller import Caller
from crm.member_settings import (COLLAPSED_MAX, DEFAULTS, ROOM_ORDER_MAX, Invalid, MemberSettings,
                                 PersonOnly)
from crm.store import Store

ORG = 'one'
ME = Caller(subject='7', workspaces=('sales',))
OTHER = Caller(subject='8', workspaces=('sales',))
WATCHED = Caller(subject='7', workspaces=('sales',), delegated=True, regime='human', shared=False, session='pchat-1')
ROUTINE = Caller(subject='7', workspaces=('sales',), delegated=True, regime='autonomous', session='flow-1')
SHARED = Caller(subject='7', workspaces=('sales',), delegated=True, regime='human', shared=True, session='chat-2')
CONTRACT = Path(__file__).parents[1] / 'contracts/crm.v1'
SCHEMA = json.loads((CONTRACT / 'crm.schema.json').read_text())


def conforms(shape, data, case=None):
    errors = list(Draft202012Validator({'$ref': '#/$defs/' + shape, '$defs': SCHEMA['$defs']}).iter_errors(data))
    assert not errors, errors[0].message
    if case and os.environ.get('CRM_WRITE_GOLDENS') == '1':
        (CONTRACT / 'golden' / f'{shape}.{case}.json').write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


@pytest.fixture
def ms():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    s.metadata.create_all(engine)
    at = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
    return MemberSettings(Store(engine), clock=lambda: at)


def rows(ms):
    with ms.store.engine.connect() as c:
        return c.execute(select(s.member_settings)).mappings().all()


def test_the_table_is_the_relations_own_and_crm_api_may_write_it():
    t = s.member_settings
    assert [c.name for c in t.primary_key.columns] == ['org_id', 'person', 'workspace']
    assert {'pinned', 'hidden', 'muted', 'notify', 'room_order', 'level', 'collapsed', 'updated_at'} <= set(t.c.keys())
    assert t.c.level.nullable and not t.c.notify.nullable
    assert migrate.WRITES['crm_member_settings'] == 'INSERT, UPDATE'
    assert s.SCHEMA_VERSION >= 5          # 6: field classes + audit columns (D4)


def test_an_existing_v4_database_upgrades_to_v5_and_keeps_its_rows():
    from sqlalchemy import inspect, text
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    with engine.begin() as c:
        for table in s.metadata.sorted_tables:
            if table.name != 'crm_member_settings':
                table.create(c)
        c.execute(text('INSERT INTO crm_schema (version) VALUES (4)'))
        c.execute(s.workspace_rosters.insert().values(org_id=ORG, workspace='growth', subject='7', role='owner'))
    with engine.begin() as c:
        migrate.tables_and_version(c)
    assert 'crm_member_settings' in set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert c.execute(text('SELECT version FROM crm_schema')).scalar_one() == s.SCHEMA_VERSION
        assert c.execute(select(s.workspace_rosters)).first() is not None


def test_a_roster_snapshot_never_touches_a_persons_settings(ms):
    """The roster mirror is replaced whole on every workspace commit; the settings live beside it."""
    ms.set(ORG, ME, 'growth', {'pinned': True})
    ms.store.apply_workspace_snapshot(ORG, 'growth', 'c1', '2026-10-16T09:00:00+00:00', [],
                                      [{'subject': '7', 'role': 'owner'}])
    ms.store.apply_workspace_snapshot(ORG, 'growth', 'c2', '2026-10-16T09:01:00+00:00', [], [])
    assert ms.read(ORG, ME)['workspaces']['growth']['pinned'] is True


def test_nothing_set_reads_empty_and_the_defaults_are_the_documented_ones(ms):
    assert ms.read(ORG, ME) == {'workspaces': {}}
    assert DEFAULTS == {'pinned': False, 'hidden': False, 'muted': False, 'room_order': [],
                        'level': None, 'collapsed': [], 'updated_at': None}
    conforms('WorkspaceSettingsMap', ms.read(ORG, ME))


def test_a_partial_set_merges_onto_the_defaults_and_then_onto_what_is_there(ms):
    r = ms.set(ORG, ME, 'growth', {'muted': True})
    assert r == {'workspace': 'growth', 'settings': {**DEFAULTS, 'muted': True, 'updated_at': '2026-10-16T09:00:00+00:00'}}
    conforms('WorkspaceSettingsResult', r)
    r = ms.set(ORG, ME, 'growth', {'pinned': True, 'room_order': ['c1', 'c2'], 'level': 3})
    assert r['settings'] == {**DEFAULTS, 'muted': True, 'pinned': True,
                             'room_order': ['c1', 'c2'], 'level': 3, 'updated_at': '2026-10-16T09:00:00+00:00'}
    r = ms.set(ORG, ME, 'growth', {'level': None, 'collapsed': ['done']})
    assert r['settings']['level'] is None and r['settings']['collapsed'] == ['done'] and r['settings']['pinned']
    conforms('WorkspaceSettingsResult', r, 'set')
    assert ms.read(ORG, ME) == {'workspaces': {'growth': r['settings']}}
    conforms('WorkspaceSettingsMap', ms.read(ORG, ME), 'one-workspace')
    assert len(rows(ms)) == 1


def test_personal_is_the_persons_own_slug(ms):
    assert ms.set(ORG, ME, 'personal', {'hidden': True})['settings']['hidden'] is True
    assert set(ms.read(ORG, ME)['workspaces']) == {'personal'}


@pytest.mark.parametrize('bad', [
    {'notify': 'loud'}, {'notify': None},
    {'level': 0}, {'level': 6}, {'level': '3'}, {'level': True}, {'level': 2.0},
    {'pinned': 'yes'}, {'pinned': 1}, {'pinned': None}, {'hidden': None}, {'muted': 0},
    {'room_order': [f'c{i}' for i in range(ROOM_ORDER_MAX + 1)]},
    {'room_order': 'c1'}, {'room_order': [1]}, {'room_order': ['bad id/x']}, {'room_order': None},
    {'collapsed': [f's{i}' for i in range(COLLAPSED_MAX + 1)]}, {'collapsed': [None]},
    {'colour': 'red'}, {'updated_at': 'x'}, {'person': '8'}, {},
])
def test_invalid_settings_are_refused_whole_and_nothing_is_written(ms, bad):
    with pytest.raises(Invalid) as e:
        ms.set(ORG, ME, 'growth', {'muted': True, **bad} if bad and 'muted' not in bad else bad)
    assert e.value.status == 422
    assert rows(ms) == []


def test_the_caps_are_inclusive(ms):
    r = ms.set(ORG, ME, 'growth', {'room_order': [f'c{i}' for i in range(ROOM_ORDER_MAX)],
                                   'collapsed': [f's{i}' for i in range(COLLAPSED_MAX)], 'level': 5})
    assert len(r['settings']['room_order']) == ROOM_ORDER_MAX and len(r['settings']['collapsed']) == COLLAPSED_MAX
    assert ms.set(ORG, ME, 'growth', {'level': 1})['settings']['level'] == 1


def test_a_repeated_id_keeps_its_first_place(ms):
    assert ms.set(ORG, ME, 'growth', {'room_order': ['a', 'b', 'a']})['settings']['room_order'] == ['a', 'b']


@pytest.mark.parametrize('slug', ['', '../x', 'a/b', 'x' * 129, '-lead', 'a..b', None, 3])
def test_a_bad_slug_is_refused(ms, slug):
    with pytest.raises(Invalid):
        ms.set(ORG, ME, slug, {'muted': True})
    assert rows(ms) == []


def test_a_person_sees_only_their_own_rows(ms):
    ms.set(ORG, ME, 'growth', {'muted': True})
    ms.set(ORG, OTHER, 'growth', {'pinned': True})
    ms.set(ORG, OTHER, 'finance', {'hidden': True})
    mine = ms.read(ORG, ME)['workspaces']
    assert set(mine) == {'growth'} and mine['growth']['muted'] and not mine['growth']['pinned']
    theirs = ms.read(ORG, OTHER)['workspaces']
    assert set(theirs) == {'growth', 'finance'} and not theirs['growth']['muted']


def test_settings_carry_no_access(ms):
    """A slug the caller is not a member of is still only their own private row: nothing is granted,
    and the answer says nothing about the workspace."""
    r = ms.set(ORG, ME, 'finance', {'pinned': True})
    assert set(r) == {'workspace', 'settings'} and set(r['settings']) == set(DEFAULTS)
    assert 'finance' not in ME.workspaces
    with ms.store.engine.connect() as c:
        assert c.execute(select(s.workspace_rosters)).first() is None
        assert c.execute(select(s.workspace_bindings)).first() is None


def test_the_watched_worker_writes_for_its_person(ms):
    assert ms.set(ORG, WATCHED, 'growth', {'muted': True})['settings']['muted'] is True
    assert ms.read(ORG, ME)['workspaces']['growth']['muted'] is True


@pytest.mark.parametrize('caller', [ROUTINE, SHARED, Caller(subject='', internal=True), Caller(subject='')])
def test_anyone_else_is_refused_person_only_and_nothing_is_written(ms, caller):
    with pytest.raises(PersonOnly) as e:
        ms.set(ORG, caller, 'growth', {'muted': True})
    assert e.value.status == 403 and e.value.body['reason'] == 'person_only'
    conforms('Refusal', e.value.body, 'person-only')
    assert rows(ms) == []


def test_organisations_do_not_see_each_others_rows(ms):
    ms.set(ORG, ME, 'growth', {'muted': True})
    assert ms.read('two', ME) == {'workspaces': {}}


def test_a_shared_turn_reads_no_settings(ms):
    """Other people read a shared turn, so it answers none of the person's own settings."""
    ms.set(ORG, ME, 'sales', {'pinned': True, 'muted': True})
    with pytest.raises(PersonOnly):
        ms.read(ORG, SHARED)
    assert ms.read(ORG, WATCHED)['workspaces']['sales']['pinned'] is True
