"""A ROOM WITH PEOPLE FROM OUTSIDE THE ORG NEVER READS THE ORG'S PRIVATE FACTS (rooms, K1, 2026-10-16).

A room's turn runs as the workspace's agent with a `shared` delegation; when some of the room's
readers are outside the org, the token also carries `scope.audience: cross_org`, identity passes it
on, and every record crm-api answers that turn is projected to its public fields — never its stage,
owner, amounts or narrative. The same record in the org's own room answers in full.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

import pytest

from crm import schema as s
from crm.bootstrap import bootstrap_org
from crm.caller import Caller, from_validate
from crm.store import PUBLIC_FIELDS, Store

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


def _bound_company(store):
    rid = store.create(ORG, ADMIN, 'Company', {'Name': 'Globex', 'Domain': 'globex.test', 'Stage': 'Negotiation',
                                               'Owner': 'p-7'}, reason='fixture', idempotency_key='co-globex',
                       match=['domain:globex.test'])['record_id']
    store.apply_workspace_snapshot(ORG, 'team', 'c1', '2026-10-16T10:00:00Z', [],
                                   [{'subject': '2', 'role': 'contributor'}])
    store.bind(ORG, ADMIN, 'team', rid)
    return rid


def _room_turn(cross_org: bool) -> Caller:
    body = {'user_id': 2, 'email': 'u2@acme.test', 'workspaces': ['team'], 'writable_workspaces': ['team'],
            'delegation': {'regime': 'human', 'workspaces': ['team'], 'shared': True,
                           **({'audience': 'cross_org'} if cross_org else {})}}
    return from_validate('vxd_x.y.z', body)


def test_a_cross_org_room_reads_public_fields_only(store):
    rid = _bound_company(store)
    rec = store.get(ORG, _room_turn(cross_org=True), rid)
    assert rec['fields'] == {'Name': 'Globex', 'Domain': 'globex.test'}
    assert rec['narrative'] is None and rec['audience'] == 'cross_org'
    listed = store.search(ORG, _room_turn(cross_org=True), 'Company')['records']
    assert listed and all(set(r['fields']) <= set(PUBLIC_FIELDS) for r in listed)
    assert not any('Stage' in r['fields'] or 'Owner' in r['fields'] for r in listed)


def test_the_orgs_own_room_reads_the_record_in_full(store):
    rid = _bound_company(store)
    rec = store.get(ORG, _room_turn(cross_org=False), rid)
    assert rec['fields']['Stage'] == 'Negotiation' and 'audience' not in rec


def test_audience_counts_only_beside_shared():
    c = from_validate('vxd_x.y.z', {'user_id': 2, 'delegation': {'regime': 'human', 'workspaces': ['team'],
                                                                 'audience': 'cross_org'}})
    assert c.cross_org is False
