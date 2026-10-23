"""The CRM's migrations path checks, before it writes anything, that its credential may make or
re-assert the CRM's own role, and otherwise stops naming the grant the database team runs.

A managed Postgres gives the application's user no superuser. Postgres refuses ALTER ROLE that names
SUPERUSER, REPLICATION or BYPASSRLS (16+: CREATEDB) from a non-superuser even to turn them off, and
from 16 a CREATEROLE user changes only roles it holds the ADMIN option on. The real-Postgres half is
``test_pg_runtime_role.py``."""
import pytest

from crm import migrate

PW = 'a' * 40
NO_ROLE = None
PLAIN_ROLE = (False, False, False, False, False, False, True)


class _Res:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _Conn:
    """Answers the three catalogue reads ``plan_role`` makes, and records every statement."""

    def __init__(self, me, target=NO_ROLE, admin=False):
        self.me, self.target, self.admin, self.log = me, target, admin, []

    def execute(self, stmt, params=None):
        sql = ' '.join(str(stmt).split())
        self.log.append(sql)
        if 'server_version_num' in sql:
            return _Res(self.me)
        if 'rolcanlogin' in sql:
            return _Res(self.target)
        if 'pg_has_role' in sql:
            return _Res((self.admin,))
        return _Res(None)


def test_a_credential_without_createrole_is_refused_naming_the_grant():
    c = _Conn(me=('vexa', False, False, 160000))
    with pytest.raises(migrate.PrivilegeMissing) as e:
        migrate.plan_role(c, 'vexa_crm')
    assert 'ALTER ROLE "vexa" CREATEROLE;' in str(e.value)
    assert 'CREATE ROLE "vexa_crm" LOGIN NOINHERIT' in str(e.value)
    assert 'database-privileges-on-an-external-postgres' in str(e.value)


def test_createrole_creates_and_a_superuser_reasserts():
    assert migrate.plan_role(_Conn(me=('vexa', False, True, 160000)), 'vexa_crm') == 'create'
    assert migrate.plan_role(_Conn(me=('postgres', True, True, 160000), target=PLAIN_ROLE), 'vexa_crm') == 'reassert'


@pytest.mark.parametrize('version,admin', [(160000, True), (150007, False)])
def test_createrole_with_reach_over_the_role_alters_it(version, admin):
    assert migrate.plan_role(_Conn(me=('vexa', False, True, version), target=PLAIN_ROLE, admin=admin),
                             'vexa_crm') == 'alter'


def test_on_postgres_16_a_role_without_the_admin_option_is_refused_naming_it():
    c = _Conn(me=('vexa', False, True, 160000), target=PLAIN_ROLE, admin=False)
    with pytest.raises(migrate.PrivilegeMissing) as e:
        migrate.plan_role(c, 'vexa_crm', logs_in=lambda: False)
    assert 'GRANT "vexa_crm" TO "vexa" WITH ADMIN OPTION;' in str(e.value)


def test_a_pre_created_role_whose_password_logs_in_is_kept():
    c = _Conn(me=('vexa', False, False, 160000), target=PLAIN_ROLE)
    assert migrate.plan_role(c, 'vexa_crm', logs_in=lambda: True) == 'keep'


def test_a_role_with_admin_power_is_refused_naming_what_to_remove():
    c = _Conn(me=('vexa', False, True, 160000), target=(True, False, False, False, True, False, True), admin=True)
    with pytest.raises(migrate.PrivilegeMissing) as e:
        migrate.plan_role(c, 'vexa_crm')
    assert 'ALTER ROLE "vexa_crm" NOSUPERUSER NOBYPASSRLS;' in str(e.value)


def test_init_stops_before_any_ddl(monkeypatch):
    """The check runs before ``migrate``: a refused credential leaves the database untouched."""
    c = _Conn(me=('vexa', False, False, 160000))

    class _Engine:
        def connect(self):
            class _Ctx:
                def __enter__(self_):
                    return c

                def __exit__(self_, *a):
                    return False
            return _Ctx()

    monkeypatch.setattr(migrate, 'migrate', lambda *a, **k: pytest.fail('DDL ran before the privilege check'))
    with pytest.raises(migrate.PrivilegeMissing):
        migrate.init(_Engine(), role='vexa_crm', password=PW, database='vexa')
    assert not any(s.startswith(('CREATE', 'ALTER', 'GRANT')) for s in c.log)
