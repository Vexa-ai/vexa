"""PRD 0.13.4 D17/D20, UI 7cc: a page's ``{{crm …}}`` block, read for one viewer (`blocks.py`).

Deny test 22 (crm half): a block renders byte-identical whether hidden records exist or not — no
rows, totals or markers betray them. Plus the "only you see this" split: a field the viewer reads
but the page's workspace may not comes back ``shared: false``.
"""
import json

import pytest

from crm.blocks import BlockRefused, read_block
from crm.caller import Caller

from test_field_classes import ADMIN, ORG, company, restrict, room, store  # noqa: F401  (fixture)

OWNER = Caller(subject='5', workspaces=('team',))
MEMBER = Caller(subject='6', workspaces=('team',))
ROSTER = [{'subject': '5', 'role': 'owner'}, {'subject': '6', 'role': 'viewer'}]


def _bytes(out):
    return json.dumps(out, sort_keys=True, separators=(',', ':')).encode()


def _person(store, name, company_id, *workspaces, key):
    rid = store.create(ORG, ADMIN, 'Person', {'Name': name, 'Company': company_id, 'LastContact': '2026-10-02'},
                       reason='fixture', idempotency_key=key)['record_id']
    for w in workspaces:
        store.bind(ORG, ADMIN, w, rid)
    return rid


def test_owner_sees_private_fields_marked_only_for_them(store):
    company(store, 'team', owner=OWNER, roster=ROSTER)
    restrict(store, '6', 'Company', ['Name', 'Domain', 'LastContact'])
    out = read_block(store, ORG, OWNER, 'team', 'company', {'company': 'Globex'})
    fields = {f['name']: f['shared'] for f in out['rows'][0]['fields']}
    assert fields['Name'] is True and fields['Domain'] is True
    assert fields['LastContact'] is False          # private: the owner's alone
    assert fields['Stage'] is False                # a member may not read it, so the room may not


def test_member_sees_fewer_fields_and_no_only_you_marker(store):
    company(store, 'team', owner=OWNER, roster=ROSTER)
    restrict(store, '6', 'Company', ['Name', 'Domain', 'LastContact'])
    out = read_block(store, ORG, MEMBER, 'team', 'company', {'company': 'Globex'})
    names = [f['name'] for f in out['rows'][0]['fields']]
    assert names == ['Domain', 'Name']
    assert all(f['shared'] for f in out['rows'][0]['fields'])


def test_deny_22_hidden_records_change_nothing(store):
    """A record the viewer cannot read changes not one byte of the block."""
    cid = company(store, 'team', owner=OWNER, roster=ROSTER)
    restrict(store, '6', 'Person', ['Name', 'Company'])
    _person(store, 'Marvin Hinz', cid, 'team', key='p1')
    before = {view: _bytes(read_block(store, ORG, MEMBER, 'team', view, args))
              for view, args in (('people', {'company': 'Globex'}), ('person', {'person': 'Hidden Harper'}),
                                 ('deals', {}))}
    _person(store, 'Hidden Harper', cid, key='p2')                    # bound nowhere: hidden from 6
    store.create(ORG, ADMIN, 'Deal', {'Name': 'Secret deal', 'Stage': 'negotiation', 'Company': cid},
                 reason='fixture', idempotency_key='d1')
    after = {view: _bytes(read_block(store, ORG, MEMBER, 'team', view, args))
             for view, args in (('people', {'company': 'Globex'}), ('person', {'person': 'Hidden Harper'}),
                                ('deals', {}))}
    assert before == after
    assert json.loads(after['people'])['rows'][0]['title'] == 'Marvin Hinz'
    assert b'total' not in after['people'] and b'next' not in after['people']
    # …and the owner, who may read them, does see them (the test is not vacuous)
    assert len(read_block(store, ORG, ADMIN, 'team', 'people', {'company': 'Globex'})['rows']) == 2


def test_unknown_view_and_unknown_company_are_the_same_empty_answer(store):
    company(store, 'team', owner=OWNER, roster=ROSTER)
    assert read_block(store, ORG, MEMBER, 'team', 'people', {'company': 'Nope'}) == {'view': 'people', 'rows': []}
    assert read_block(store, ORG, MEMBER, 'team', 'secrets', {}) == {'view': 'secrets', 'rows': []}


def test_a_room_is_refused(store):
    company(store, 'team', owner=OWNER, roster=ROSTER)
    with pytest.raises(BlockRefused):
        read_block(store, ORG, room('team'), 'team', 'company', {'company': 'Globex'})
