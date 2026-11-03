"""crm-api's RUNTIME ROLE CAN DO WHAT crm-api ASKS OF IT, ON REAL POSTGRES.

The rest of the suite runs on SQLite, which has no roles, grants or triggers. This file runs the
migration as the migration credential, then logs in as the runtime role (``vexa_crm``) exactly as
crm-api does, and proves the two runtime operations that write outside that role's grants work:

- making an individual's personal CRM on first use (``bootstrap.ensure_personal_org``);
- erasing it on account delete (``Store.erase_org``), which leaves the append-only tables alone
  (``store.ERASE_APPEND_ONLY``) and says so.

It is skipped unless ``CRM_TEST_PG_ADMIN_URL`` names an admin login on a THROWAWAY Postgres. It
creates (and drops) its own database there, and creates or re-asserts the ``vexa_crm`` role. Run:

    pw=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
    docker run -d --name crm-pg-test -e POSTGRES_PASSWORD="$pw" -p 127.0.0.1:55432:5432 postgres:17-alpine
    CRM_TEST_PG_ADMIN_URL="postgresql+pg8000://postgres:$pw@127.0.0.1:55432/postgres" \\
        uv run --project core/crm pytest core/crm/tests/test_pg_runtime_role.py -q
    docker rm -f crm-pg-test
"""
import os
import secrets
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from crm import migrate, schema as s
from crm.bootstrap import bootstrap_org, ensure_personal_org
from crm.caller import Caller
from crm.card_ops import Cards
from crm.member_settings import MemberSettings
from crm.store import CRMError, Store

ADMIN_URL = os.environ.get('CRM_TEST_PG_ADMIN_URL', '')
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason='CRM_TEST_PG_ADMIN_URL is unset (a throwaway Postgres)')

INSTALL = 'one'
SOLO = '42'
SOLO_ORG = f'p-{SOLO}'
ADMIN = Caller(subject='1', email='admin@acme.test', is_admin=True)
ME = Caller(subject=SOLO, email='solo@solo.test')


@pytest.fixture
def engines():
    """(migration engine, runtime engine) on a fresh database; dropped afterwards."""
    server = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
    db = 'crm_rt_' + secrets.token_hex(4)
    with server.connect() as c:
        c.execute(text(f'CREATE DATABASE {db}'))
    admin = create_engine(make_url(ADMIN_URL).set(database=db))
    password = secrets.token_urlsafe(32)
    migrate.init(admin, role=migrate.DEFAULT_ROLE, password=password, database=db)
    bootstrap_org(admin, INSTALL, 'Acme')                    # what `python -m crm.migrate init` does
    url = make_url(ADMIN_URL)
    runtime = create_engine(f'postgresql+pg8000://{migrate.DEFAULT_ROLE}:{quote(password, safe="")}@'
                            f'{url.host}:{url.port or 5432}/{db}')
    try:
        migrate.check(runtime)                                   # crm-api's own boot check passes
        yield admin, runtime
    finally:
        runtime.dispose()
        admin.dispose()
        with server.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS {db} WITH (FORCE)'))
        server.dispose()


def _count(engine, table, org):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(table).where(table.c.org_id == org)).scalar_one()


def _counts(engine, org):
    return {t.name: _count(engine, t, org) for t in s.metadata.sorted_tables if 'org_id' in t.c}


def _fill_personal_crm(runtime):
    store = Store(runtime)
    cards = Cards(store)
    store.ensure_account(SOLO_ORG, ME)
    rid = store.create(SOLO_ORG, ME, 'Company', {'Name': 'Lead'}, narrative='private notes', reason='t',
                       idempotency_key='k1', match=['domain:lead.test'])['record_id']
    store.change(SOLO_ORG, ME, rid, 1, {'Stage': 'Prospect'}, reason='call')
    cards.create(SOLO_ORG, ME, 'Follow up', fields={'Next': 'Write back'})
    MemberSettings(store).set(SOLO_ORG, ME, 'personal', {'pinned': True})
    store.apply_workspace_snapshot(SOLO_ORG, 'personal', 'c1', '2026-10-16T10:00:00Z',
                                   [{'page': 'a.md', 'record': rid}], [{'subject': SOLO, 'role': 'owner'}])
    store.audit(SOLO_ORG, ME, 'read', rid)
    return store


def test_the_runtime_role_makes_a_personal_crm_on_first_use(engines):
    _, runtime = engines
    assert sorted(ensure_personal_org(runtime, SOLO_ORG, 'solo@solo.test')) == \
        ['Card', 'Company', 'Deal', 'Person', 'Project', 'org']
    assert ensure_personal_org(runtime, SOLO_ORG, 'solo@solo.test') == []        # idempotent
    store = Store(runtime)
    with runtime.connect() as c:
        assert store.org_policy(c, SOLO_ORG) == {'active': True, 'admin': True}
    # the first request a personal account makes then works as the runtime role
    assert store.ensure_account(SOLO_ORG, ME)
    assert store.search(SOLO_ORG, ME, 'Company')['records'] == []


def test_the_runtime_role_makes_only_an_individuals_org(engines):
    _, runtime = engines
    for org in (INSTALL, 'two', 'p-', 'p-1x'):
        with pytest.raises(ValueError):
            ensure_personal_org(runtime, org, 'x')
        with pytest.raises(DBAPIError):                         # the function itself refuses too
            with runtime.begin() as c:
                c.execute(text('SELECT crm_bootstrap_personal_org(:o, :n)'), {'o': org, 'n': 'x'})
    assert _count(runtime, s.orgs, 'two') == 0 and _count(runtime, s.object_types, 'two') == 0


def test_the_runtime_role_erases_a_personal_crm_and_leaves_only_the_append_only_tables(engines):
    _, runtime = engines
    ensure_personal_org(runtime, SOLO_ORG, 'solo@solo.test')
    Store(runtime).create(INSTALL, ADMIN, 'Company', {'Name': 'Acme'}, reason='t', idempotency_key='c1')
    store = _fill_personal_crm(runtime)
    install_before = _counts(runtime, INSTALL)
    before = _counts(runtime, SOLO_ORG)
    assert all(before[t] > 0 for t in ('crm_records', 'crm_revisions', 'crm_audit', 'crm_member_settings',
                                       'crm_card_members', 'crm_match_ids', 'crm_page_references', 'crm_outbox'))

    out = store.erase_org(SOLO_ORG)                              # as vexa_crm: no permission error

    after = _counts(runtime, SOLO_ORG)
    assert out['left'] == {t: before[t] for t in migrate.APPEND_ONLY}
    assert {t: after[t] for t in migrate.APPEND_ONLY} == out['left']        # untouched
    kept = set(migrate.APPEND_ONLY) | {'crm_records', 'crm_object_definitions'}
    assert {t: n for t, n in after.items() if t not in kept and n} == {}    # everything else is gone
    assert out['rows'] == sum(before[t] - after[t] for t in before)
    # the records the kept revisions point at stay as empty, deleted rows
    with runtime.connect() as c:
        rows = c.execute(select(s.records).where(s.records.c.org_id == SOLO_ORG)).mappings().all()
    assert rows and all(r['deleted'] and r['fields'] == {} and r['narrative'] == '' and r['account_id'] is None
                        for r in rows)
    assert out['tombstoned'] == {'crm_records': len(rows)}
    assert _counts(runtime, INSTALL) == install_before          # the company's CRM is untouched
    again = store.erase_org(SOLO_ORG)
    assert again['rows'] == 0                                    # idempotent


def test_the_runtime_role_still_cannot_delete_records_itself_or_erase_another_org(engines):
    _, runtime = engines
    Store(runtime).create(INSTALL, ADMIN, 'Company', {'Name': 'Acme'}, reason='t', idempotency_key='c1')
    with pytest.raises(DBAPIError):
        with runtime.begin() as c:
            c.execute(text('DELETE FROM crm_records'))
    with pytest.raises((CRMError, DBAPIError)):
        Store(runtime).erase_org(INSTALL)
    with pytest.raises(DBAPIError):
        with runtime.begin() as c:
            c.execute(text("SELECT crm_erase_personal_org('one')"))
    assert _count(runtime, s.records, INSTALL) == 1
