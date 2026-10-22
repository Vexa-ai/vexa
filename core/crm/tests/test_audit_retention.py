"""THE AUDIT EXPIRES, AND STAYS APPEND-ONLY FOR AS LONG AS IT IS KEPT (M5 retention).

`crm_audit` names every read and write with the caller, the session and the trace — person data,
kept until now without end: the 2026-10-18 gate found "no retention or expiry policy for
crm_audit", and the final 0.13.4 gate "no crm_audit retention or purge in core/crm/src".

The period is `CRM_AUDIT_RETENTION_DAYS` (default 365, 0 keeps for ever), written by the migrations
path into `crm_purge_audit()`; crm-api's role may run that function and nothing else that deletes.
The Postgres half runs against a throwaway server (`CRM_TEST_PG_ADMIN_URL`, as
`test_pg_runtime_role.py`); the declaration half runs everywhere."""
import inspect
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from crm import api, migrate, schema as s

ADMIN_URL = os.environ.get('CRM_TEST_PG_ADMIN_URL', '')
pg = pytest.mark.skipif(not ADMIN_URL, reason='CRM_TEST_PG_ADMIN_URL is unset (a throwaway Postgres)')
ORG = 'one'


def _at(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _purge(runtime):
    from crm import retention
    return retention.purge_audit(runtime)


def _engines(audit_days=None):
    server = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
    db = 'crm_ret_' + secrets.token_hex(4)
    with server.connect() as c:
        c.execute(text(f'CREATE DATABASE {db}'))
    admin = create_engine(make_url(ADMIN_URL).set(database=db))
    password = secrets.token_urlsafe(32)
    period = {} if audit_days is None else {'audit_days': audit_days}
    migrate.init(admin, role=migrate.DEFAULT_ROLE, password=password, database=db, **period)
    url = make_url(ADMIN_URL)
    runtime = create_engine(f'postgresql+pg8000://{migrate.DEFAULT_ROLE}:{quote(password, safe="")}@'
                            f'{url.host}:{url.port or 5432}/{db}')
    migrate.check(runtime)

    def drop():
        runtime.dispose()
        admin.dispose()
        with server.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS {db} WITH (FORCE)'))
        server.dispose()
    return admin, runtime, drop


@pytest.fixture
def kept_90():
    admin, runtime, drop = _engines(90)
    try:
        yield admin, runtime
    finally:
        drop()


@pytest.fixture
def default_period():
    admin, runtime, drop = _engines()
    try:
        yield admin, runtime
    finally:
        drop()


def _audit(runtime, rid, days_ago):
    """One audit row as crm-api writes it (its role holds INSERT only)."""
    with runtime.begin() as c:
        c.execute(s.audit.insert().values(org_id=ORG, id=rid, at=_at(days_ago), subject='7',
                                          caller='person', session='s', trace='t', op='read',
                                          record_id='r1', outcome='ok'))


def _ids(engine):
    with engine.connect() as c:
        return sorted(r[0] for r in c.execute(text('SELECT id FROM crm_audit')))


def _refused(engine, *sql):
    with pytest.raises(DBAPIError) as e:
        with engine.begin() as c:
            for statement in sql:
                c.execute(text(statement))
    return str(e.value)


@pg
def test_the_purge_removes_exactly_the_rows_past_the_period(kept_90):
    admin, runtime = kept_90
    for rid, age in (('old-1', 400), ('old-2', 91), ('young', 89), ('today', 0)):
        _audit(runtime, rid, age)
    assert _purge(runtime) == 2                 # as crm-api runs it, its own role
    assert _ids(admin) == ['today', 'young']
    assert _purge(runtime) == 0, 'a second replica finds nothing'


@pg
def test_by_default_a_row_older_than_a_year_expires_and_a_younger_one_stays(default_period):
    """The deployed default, through SQL alone: crm-api's role runs the purge and a row past 365
    days is gone; one inside the year stays."""
    admin, runtime = default_period
    _audit(runtime, 'past-a-year', 366)
    _audit(runtime, 'inside-a-year', 364)
    with runtime.begin() as c:
        assert c.execute(text('SELECT crm_purge_audit()')).scalar_one() == 1
    assert _ids(admin) == ['inside-a-year']


@pg
def test_inside_the_period_the_audit_is_still_append_only(default_period):
    """POSITIVE CONTROL. Nothing about a row younger than the period (the default year) changed:
    no UPDATE, no DELETE, no TRUNCATE — not by crm-api's role, not by the owner, not even by the
    owner holding the purge's own cutoff setting."""
    admin, runtime = default_period
    _audit(runtime, 'young', 10)
    _audit(runtime, 'old', 200)
    assert 'permission denied' in _refused(runtime, "DELETE FROM crm_audit WHERE id = 'young'")
    assert 'permission denied' in _refused(runtime, "DELETE FROM crm_audit WHERE id = 'old'")
    assert 'permission denied' in _refused(runtime, "UPDATE crm_audit SET outcome = 'x'")
    assert 'append-only' in _refused(admin, "UPDATE crm_audit SET outcome = 'x' WHERE id = 'old'")
    assert 'append-only' in _refused(admin, "TRUNCATE crm_audit")
    assert 'append-only' in _refused(admin, "DELETE FROM crm_audit WHERE id = 'old'")
    assert 'append-only' in _refused(admin, "DELETE FROM crm_audit WHERE id = 'young'")
    assert 'append-only' in _refused(
        admin, "SELECT set_config('crm.audit_expiring', 'on', true)",
        "DELETE FROM crm_audit WHERE id = 'young'")
    assert _ids(admin) == ['old', 'young']


@pg
def test_the_runtime_role_can_neither_choose_nor_rewrite_the_period(kept_90):
    admin, runtime = kept_90
    _audit(runtime, 'mid', 60)
    assert 'does not exist' in _refused(runtime, 'SELECT crm_purge_audit(1)')
    for sql in (migrate.audit_purge_function_sql(1), migrate.audit_expire_trigger_sql(1)):
        with pytest.raises(DBAPIError) as e:
            with runtime.begin() as c:
                c.exec_driver_sql(sql)
        assert 'must be owner' in str(e.value) or 'permission denied' in str(e.value)
    assert _purge(runtime) == 0
    assert _ids(admin) == ['mid']


@pg
def test_the_other_append_only_tables_still_refuse_every_delete(default_period):
    admin, _ = default_period
    for table in ('crm_revisions', 'crm_card_layouts', 'crm_desk_layouts'):
        assert 'append-only' in _refused(admin, f'DELETE FROM {table}')


@pg
def test_zero_keeps_the_audit_for_ever():
    admin, runtime, drop = _engines(0)
    try:
        _audit(runtime, 'ancient', 3650)
        assert _purge(runtime) == 0
        assert _ids(admin) == ['ancient']
        assert 'append-only' in _refused(admin, 'DELETE FROM crm_audit')
    finally:
        drop()


@pg
def test_init_again_keeps_rows_and_takes_the_new_period(kept_90):
    """`crm.migrate init` runs on every deploy: re-running it keeps every row, and a new period
    (here a longer one) is what the next purge applies."""
    admin, runtime = kept_90
    _audit(runtime, 'a', 100)
    migrate.migrate(admin, audit_days=120)
    assert _ids(admin) == ['a']
    assert _purge(runtime) == 0
    migrate.migrate(admin, audit_days=90)
    assert _purge(runtime) == 1


def test_the_setting_the_trigger_reads_is_the_one_the_purge_sets():
    assert migrate.AUDIT_EXPIRE_SETTING == 'crm.audit_expiring'
    assert migrate.AUDIT_EXPIRE_SETTING in migrate.audit_purge_function_sql(90)
    assert migrate.AUDIT_EXPIRE_SETTING in migrate.audit_expire_trigger_sql(90)


def test_the_period_is_declared_parsed_and_guarded():
    assert migrate.audit_retention_days({}) == migrate.AUDIT_RETENTION_DEFAULT_DAYS == 365
    assert migrate.audit_retention_days({'CRM_AUDIT_RETENTION_DAYS': '0'}) == 0
    assert migrate.audit_retention_days({'CRM_AUDIT_RETENTION_DAYS': '730'}) == 730
    for bad in ('1', '29', '-5', 'a year'):
        with pytest.raises(ValueError):
            migrate.audit_retention_days({'CRM_AUDIT_RETENTION_DAYS': bad})
    import json
    from pathlib import Path
    decl = json.loads((Path(migrate.__file__).parent / 'config.v1.json').read_text())
    key = next(k for k in decl['keys'] if k['key'] == 'CRM_AUDIT_RETENTION_DAYS')
    assert (key['class'], key['default']) == ('defaulted', '365')


def test_the_runtime_role_is_granted_the_purge_and_crm_api_runs_it():
    assert 'PURGE_AUDIT_FN' in inspect.getsource(migrate.init)
    assert 'retention.start(engine)' in inspect.getsource(api.configured_app)
    assert 'crm_audit' in migrate.APPEND_ONLY


def test_the_purge_loop_survives_a_failed_pass():
    calls = []

    class _Boom:
        def begin(self):
            calls.append(1)
            raise RuntimeError('db down')

    import threading
    from crm import retention
    stop = threading.Event()
    stop.set()
    retention.run(_Boom(), every_s=0, stop=stop)
    assert calls == [1]
