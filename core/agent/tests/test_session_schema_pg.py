"""The session store's migrations path on REAL Postgres, run by a credential that is not a superuser
(an external, managed Postgres): it either makes the store's role and the store then creates
sessions, or it stops before writing anything and names the grant the database team runs.

Skipped unless ``SESSIONS_TEST_PG_ADMIN`` names a superuser on a THROWAWAY Postgres as
``user:password@host:port``. Each test creates (and drops) its own database and roles there. Run:

    pw=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
    docker run -d --name sessions-pg-test -e POSTGRES_PASSWORD="$pw" -p 127.0.0.1:55433:5432 postgres:16
    SESSIONS_TEST_PG_ADMIN="postgres:$pw@127.0.0.1:55433" \\
        uv run --project core/agent pytest core/agent/tests/test_session_schema_pg.py -q
    docker rm -f sessions-pg-test
"""
from __future__ import annotations

import os
import secrets

import pytest

from control_plane import session_schema
from control_plane.session_store import SessionStore
from tests._session_fakes import MemBlobs

ADMIN = os.environ.get("SESSIONS_TEST_PG_ADMIN", "")
pytestmark = pytest.mark.skipif(not ADMIN, reason="SESSIONS_TEST_PG_ADMIN is unset (a throwaway Postgres)")


def _parts():
    cred, addr = ADMIN.rsplit("@", 1)
    user, password = cred.split(":", 1)
    host, port = addr.rsplit(":", 1)
    return user, password, host, int(port)


def _connect(user, password, database):
    import pg8000.dbapi

    _, _, host, port = _parts()
    conn = pg8000.dbapi.connect(user=user, password=password, host=host, port=port, database=database)
    conn.autocommit = True
    return conn


def _q(conn, sql, args=()):
    cur = conn.cursor()
    cur.execute(sql, args)
    return cur


def _super(database="postgres"):
    user, password, _, _ = _parts()
    return _connect(user, password, database)


@pytest.fixture
def cluster(monkeypatch):
    """A fresh database owned by a NON-superuser application user, the session store's role name and
    password, and the env ``session_schema init`` reads. Everything is dropped afterwards."""
    tag = secrets.token_hex(4)
    db, app, role = f"car60_{tag}", f"car60_app_{tag}", f"car60_sessions_{tag}"
    app_pw, role_pw = secrets.token_hex(16), secrets.token_hex(16)
    su = _super()
    su.cursor().execute(f'CREATE ROLE "{app}" LOGIN PASSWORD \'{app_pw}\'')
    su.cursor().execute(f'CREATE DATABASE "{db}" OWNER "{app}"')
    _, _, host, port = _parts()
    for k, v in {"DB_USER": app, "DB_PASSWORD": app_pw, "DB_HOST": host, "DB_PORT": str(port), "DB_NAME": db,
                 "VEXA_SESSIONS_DB_USER": role, "VEXA_SESSIONS_DB_PASSWORD": role_pw}.items():
        monkeypatch.setenv(k, v)
    version = int(_q(su, "SHOW server_version_num").fetchone()[0])
    yield {"su": su, "db": db, "app": app, "role": role, "role_pw": role_pw, "version": version}
    su.cursor().execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
    for r in (role, app):
        su.cursor().execute(f'DROP ROLE IF EXISTS "{r}"')
    su.close()


def _store_creates_a_session(cl):
    store = SessionStore.open_postgres(MemBlobs(), host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]),
                                       database=cl["db"], user=cl["role"], password=cl["role_pw"])
    try:
        row = store.create("u-1", title="car60", workspaces=["w-1"])
        assert row["owner"] == "u-1"
    finally:
        store.close()


def test_without_createrole_the_migrations_path_stops_naming_the_grant(cluster, capsys):
    assert session_schema.main(["x", "init"]) == 1
    out = capsys.readouterr().out
    assert f'ALTER ROLE "{cluster["app"]}" CREATEROLE;' in out and out.startswith("[session-schema] STOP:")
    # nothing written: no role, no table
    assert _q(cluster["su"], "SELECT 1 FROM pg_roles WHERE rolname = %s", (cluster["role"],)).fetchone() is None
    db = _super(cluster["db"])
    try:
        assert _q(db, "SELECT to_regclass('agent_session')").fetchone()[0] is None
    finally:
        db.close()


def test_with_createrole_the_store_is_made_and_creates_sessions(cluster, capsys):
    cluster["su"].cursor().execute(f'ALTER ROLE "{cluster["app"]}" CREATEROLE')
    assert session_schema.main(["x", "init"]) == 0, capsys.readouterr().out
    assert session_schema.main(["x", "init"]) == 0, capsys.readouterr().out   # re-run: re-asserts
    _store_creates_a_session(cluster)


def test_a_pre_created_role_needs_no_createrole(cluster, capsys):
    cluster["su"].cursor().execute(f'CREATE ROLE "{cluster["role"]}" LOGIN NOINHERIT PASSWORD \'{cluster["role_pw"]}\'')
    assert session_schema.main(["x", "init"]) == 0, capsys.readouterr().out
    _store_creates_a_session(cluster)


def test_on_16_a_role_it_did_not_create_needs_the_admin_option(cluster, capsys):
    if cluster["version"] < 160000:
        pytest.skip("the ADMIN option rule is Postgres 16+")
    su = cluster["su"].cursor()
    su.execute(f'ALTER ROLE "{cluster["app"]}" CREATEROLE')
    su.execute(f'CREATE ROLE "{cluster["role"]}" LOGIN NOINHERIT PASSWORD \'{secrets.token_hex(16)}\'')
    assert session_schema.main(["x", "init"]) == 1
    assert f'GRANT "{cluster["role"]}" TO "{cluster["app"]}" WITH ADMIN OPTION;' in capsys.readouterr().out
    su.execute(f'GRANT "{cluster["role"]}" TO "{cluster["app"]}" WITH ADMIN OPTION')
    assert session_schema.main(["x", "init"]) == 0, capsys.readouterr().out
    _store_creates_a_session(cluster)
