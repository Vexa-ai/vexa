"""The CRM's migrations path on REAL Postgres, run by a credential that is not a superuser (an
external, managed Postgres): it either makes the CRM's role, and crm-api's own check then passes, or
it stops before writing anything and names the grant the database team runs.

Skipped unless ``CRM_TEST_PG_ADMIN_URL`` names a superuser on a THROWAWAY Postgres (see
``test_pg_runtime_role.py`` for the container). Each test creates and drops its own database and
roles there."""
import os
import secrets
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from crm import migrate

ADMIN_URL = os.environ.get('CRM_TEST_PG_ADMIN_URL', '')
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason='CRM_TEST_PG_ADMIN_URL is unset (a throwaway Postgres)')


@pytest.fixture
def cluster(monkeypatch):
    tag = secrets.token_hex(4)
    db, app, role = f'car60crm_{tag}', f'car60crm_app_{tag}', f'car60crm_role_{tag}'
    app_pw, role_pw = secrets.token_hex(16), secrets.token_hex(16)
    su = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
    with su.connect() as c:
        c.execute(text(f'CREATE ROLE "{app}" LOGIN PASSWORD \'{app_pw}\''))
        c.execute(text(f'CREATE DATABASE "{db}" OWNER "{app}"'))
    url = make_url(ADMIN_URL)
    env = {'DB_USER': app, 'DB_PASSWORD': app_pw, 'DB_HOST': url.host, 'DB_PORT': str(url.port or 5432),
           'DB_NAME': db, 'CRM_DB_USER': role, 'CRM_DB_PASSWORD': role_pw, 'CRM_ORG_ID': 'acme',
           'CRM_DATABASE_URL': f'postgresql+pg8000://{role}:{quote(role_pw, safe="")}@{url.host}:{url.port or 5432}/{db}'}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    yield {'su': su, 'app': app, 'role': role, 'db': db}
    with su.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)'))
        for r in (role, app):
            c.execute(text(f'DROP ROLE IF EXISTS "{r}"'))
    su.dispose()


def test_without_createrole_the_crm_schema_job_stops_naming_the_grant(cluster, capsys):
    assert migrate.main(['x', 'init']) == 1
    out = capsys.readouterr().out
    assert out.startswith('[crm-schema] STOP:') and f'ALTER ROLE "{cluster["app"]}" CREATEROLE;' in out
    with create_engine(make_url(ADMIN_URL).set(database=cluster['db'])).connect() as c:
        assert c.execute(text("SELECT to_regclass('crm_records')")).scalar() is None


def test_with_createrole_the_crm_role_is_made_and_crm_api_check_passes(cluster, capsys, monkeypatch):
    with cluster['su'].connect() as c:
        c.execute(text(f'ALTER ROLE "{cluster["app"]}" CREATEROLE'))
    assert migrate.main(['x', 'init']) == 0, capsys.readouterr().out
    assert migrate.main(['x', 'init']) == 0, capsys.readouterr().out      # re-run: re-asserts
    monkeypatch.setenv('CRM_WAIT_SEC', '1')
    assert migrate.main(['x', 'check']) == 0, capsys.readouterr().out
