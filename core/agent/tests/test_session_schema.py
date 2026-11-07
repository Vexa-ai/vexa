"""ADR-0048 (S1, S11): agent-api reaches the session store as its own Postgres role, limited to the
agent_session* tables. The migrations path — never agent-api — creates that role and the tables,
with the instance's admin credential; agent-api checks the schema version it finds and refuses a
superuser login."""
from __future__ import annotations

import pytest

from control_plane import session_schema, session_store
from control_plane.session_store import SessionStore
from tests._session_fakes import MemBlobs


class _Cur:
    def __init__(self, log, answers):
        self.log, self.answers, self._row = log, answers, None
        self.rowcount = 1

    def execute(self, sql, args=()):
        self.log.append((" ".join(sql.split()), tuple(args)))
        self._row = None
        for needle, row in self.answers.items():
            if needle in sql:
                self._row = row
        return self

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class _Conn:
    def __init__(self, answers=None):
        self.log, self.answers = [], dict(answers or {})

    def cursor(self):
        return _Cur(self.log, self.answers)

    def close(self):
        pass


PW = "a" * 64


#: The answers the fake gives: the credential's own row (user, rolsuper, rolcreaterole, version), the
#: store role's row (rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolinherit,
#: rolcanlogin; None when absent) and whether the credential holds the ADMIN option on it.
SUPER = ("postgres", True, True, 160000)
NO_ROLE = None
PLAIN_ROLE = (False, False, False, False, False, False, True)


def _pg(me=SUPER, target=NO_ROLE, admin=False):
    return _Conn({"rolcanlogin FROM pg_roles": target, "server_version_num": me, "pg_has_role": (admin,)})


def _writes(c):
    return [s for s, _ in c.log if s.startswith(("CREATE", "ALTER", "GRANT", "INSERT"))]


def test_init_creates_a_login_role_with_no_admin_powers_and_grants_only_session_tables():
    c = _pg()
    session_schema.init(c, role="vexa_agent_sessions", password=PW, database="vexa")
    sql = [s for s, _ in c.log]
    create = next(s for s in sql if s.startswith("CREATE ROLE"))
    assert '"vexa_agent_sessions"' in create
    for flag in ("LOGIN", "NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE", "NOREPLICATION", "NOBYPASSRLS", "NOINHERIT"):
        assert flag in create
    grants = [s for s in sql if s.startswith("GRANT") and " ON " in s and "TABLE" in s or s.startswith("GRANT SELECT")]
    granted = set()
    for g in grants:
        on = g.split(" ON ", 1)[1].split(" TO ")[0].replace("TABLE ", "")
        granted |= {t.strip() for t in on.split(",")}
    assert granted and all(t.startswith("agent_session") for t in granted)
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS agent_session (") for s in sql)
    assert PW not in " ".join(f"{s} {a}" for s, a in c.log if not s.startswith(("CREATE ROLE", "ALTER ROLE")))


def test_init_on_an_existing_role_only_alters_it():
    c = _pg(target=PLAIN_ROLE)
    session_schema.init(c, role="vexa_agent_sessions", password=PW, database="vexa")
    assert not any(s.startswith("CREATE ROLE") for s, _ in c.log)
    alter = next(s for s, _ in c.log if s.startswith("ALTER ROLE"))
    for flag in ("NOSUPERUSER", "NOREPLICATION", "NOBYPASSRLS"):     # a superuser re-asserts every limit
        assert flag in alter


# ── the credential's privileges, checked before anything is written (car 60) ──────────────────────
#: A managed Postgres gives the application's user no superuser. Postgres refuses ALTER ROLE that
#: names SUPERUSER, REPLICATION or BYPASSRLS (16+: CREATEDB) from a non-superuser even to turn them
#: off, and from 16 a CREATEROLE user changes only roles it holds the ADMIN option on.
APP = ("vexa", False, True, 160000)
APP_NO_CREATEROLE = ("vexa", False, False, 160000)


def test_a_credential_without_createrole_stops_naming_the_grant_and_writes_nothing():
    c = _pg(me=APP_NO_CREATEROLE)
    with pytest.raises(session_schema.PrivilegeMissing) as e:
        session_schema.init(c, role="vexa_agent_sessions", password=PW, database="vexa")
    assert 'ALTER ROLE "vexa" CREATEROLE;' in str(e.value)
    assert 'CREATE ROLE "vexa_agent_sessions" LOGIN NOINHERIT' in str(e.value)
    assert "database-privileges-on-an-external-postgres" in str(e.value)
    assert _writes(c) == []


def test_a_createrole_credential_creates_the_role_without_naming_attributes_it_may_not_alter():
    c = _pg(me=APP)
    session_schema.init(c, role="vexa_agent_sessions", password=PW, database="vexa")
    assert any(s.startswith('CREATE ROLE "vexa_agent_sessions" WITH LOGIN NOSUPERUSER') for s in _writes(c))
    assert not any(s.startswith("ALTER ROLE") for s in _writes(c))


@pytest.mark.parametrize("version,admin", [(160000, True), (150007, False)])
def test_a_createrole_credential_re_asserts_only_login_noinherit_and_the_password(version, admin):
    c = _pg(me=("vexa", False, True, version), target=PLAIN_ROLE, admin=admin)
    session_schema.init(c, role="vexa_agent_sessions", password=PW, database="vexa")
    alter = next(s for s in _writes(c) if s.startswith("ALTER ROLE"))
    assert alter == f'ALTER ROLE "vexa_agent_sessions" WITH LOGIN NOINHERIT PASSWORD \'{PW}\''


def test_on_postgres_16_a_role_without_the_admin_option_stops_naming_it():
    c = _pg(me=APP, target=PLAIN_ROLE, admin=False)
    with pytest.raises(session_schema.PrivilegeMissing) as e:
        session_schema.init(c, role="vexa_agent_sessions", password=PW, database="vexa", logs_in=lambda: False)
    assert 'GRANT "vexa_agent_sessions" TO "vexa" WITH ADMIN OPTION;' in str(e.value)
    assert "CREATEROLE;" not in str(e.value)
    assert _writes(c) == []


def test_a_pre_created_role_whose_password_logs_in_needs_no_role_privilege():
    c = _pg(me=APP_NO_CREATEROLE, target=PLAIN_ROLE)
    session_schema.init(c, role="vexa_agent_sessions", password=PW, database="vexa", logs_in=lambda: True)
    assert not any(s.startswith(("CREATE ROLE", "ALTER ROLE")) for s in _writes(c))
    assert any(s.startswith("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE agent_session") for s in _writes(c))


def test_a_role_with_admin_power_stops_naming_what_to_remove():
    c = _pg(me=APP, target=(False, False, False, True, True, False, True), admin=True)
    with pytest.raises(session_schema.PrivilegeMissing) as e:
        session_schema.init(c, role="vexa_agent_sessions", password=PW, database="vexa")
    assert 'ALTER ROLE "vexa_agent_sessions" NOREPLICATION NOBYPASSRLS;' in str(e.value)
    assert _writes(c) == []


def test_the_migrations_path_prints_the_refusal_and_exits_non_zero(monkeypatch, capsys):
    c = _pg(me=APP_NO_CREATEROLE)
    monkeypatch.setenv("VEXA_SESSIONS_DB_PASSWORD", PW)
    monkeypatch.setattr(session_schema, "_admin_conn", lambda: c)
    monkeypatch.setattr(session_schema, "_logs_in", lambda: False)
    assert session_schema.main(["x", "init"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("[session-schema] STOP: database user \"vexa\" may not create") and "CREATEROLE" in out


@pytest.mark.parametrize("role,pw", [("postgres", PW), ('x"; DROP', PW), ("vexa_agent_sessions", "short"),
                                     ("vexa_agent_sessions", "a' OR '1" + "a" * 20)])
def test_init_refuses_an_admin_or_unquotable_role_or_password(role, pw):
    with pytest.raises(ValueError):
        session_schema.init(_Conn(), role=role, password=pw, database="vexa")


def test_agent_api_refuses_a_superuser_login():
    c = _Conn({"rolsuper": (True,), "agent_session_schema": (session_store.SCHEMA_VERSION,)})
    with pytest.raises(session_schema.SchemaNotReady, match="superuser"):
        session_schema.check(c)


def test_agent_api_refuses_a_schema_version_it_does_not_know():
    c = _Conn({"rolsuper": (False,), "agent_session_schema": (session_store.SCHEMA_VERSION + 1,)})
    with pytest.raises(session_schema.SchemaNotReady, match="version"):
        session_schema.check(c)


def test_agent_api_runs_no_ddl_against_postgres(monkeypatch):
    c = _Conn({"rolsuper": (False,), "agent_session_schema": (session_store.SCHEMA_VERSION,)})
    monkeypatch.setattr(session_store, "_connect_postgres",
                        lambda **p: (c, (lambda sql: sql), "postgres"))
    s = SessionStore.open_postgres(MemBlobs(), host="h", port=5432, database="vexa",
                                   user="vexa_agent_sessions", password=PW)
    assert s is not None
    assert not any(sql.startswith(("CREATE", "ALTER", "GRANT", "INSERT")) for sql, _ in c.log)


def test_from_settings_uses_the_sessions_role_not_the_instance_user(monkeypatch):
    seen = {}

    class _S:
        sessions_db_url = ""
        db_host, db_port, db_name = "postgres", 5432, "vexa"
        sessions_db_user, sessions_db_password = "vexa_agent_sessions", PW
        sessions_s3_endpoint, sessions_s3_secure, sessions_s3_bucket = "storage:9000", False, "vexa-sessions"
        sessions_s3_region, sessions_s3_access_key, sessions_s3_secret_key = "us-east-1", "a", "b"

    monkeypatch.setattr(SessionStore, "open_postgres",
                        classmethod(lambda cls, blobs, **p: seen.update(p) or "store"))
    assert session_store.from_settings(_S()) == "store"
    assert seen["user"] == "vexa_agent_sessions" and seen["password"] == PW


def test_from_settings_is_off_without_the_sessions_role_password():
    class _S:
        sessions_db_url = ""
        db_host = "postgres"
        sessions_db_user, sessions_db_password = "vexa_agent_sessions", ""
        sessions_s3_endpoint = "storage:9000"
    assert session_store.from_settings(_S()) is None


def test_from_settings_is_off_without_the_stores_own_s3_key():
    """Never boto3's ambient credential chain (an instance profile, the env's AWS_* pair)."""
    class _S:
        sessions_db_url = ""
        db_host, db_port, db_name = "postgres", 5432, "vexa"
        sessions_db_user, sessions_db_password = "vexa_agent_sessions", PW
        sessions_s3_endpoint, sessions_s3_bucket = "http://storage:9000", "vexa-sessions"
        sessions_s3_access_key, sessions_s3_secret_key = "", ""
    assert session_store.from_settings(_S()) is None


def test_every_table_the_store_creates_is_granted_to_its_role():
    """The role's grants are derived from the schema of record, so a table another change adds to
    ``_DDL`` (calls, proposals) is granted with it and never left readable only by the admin."""
    import re

    from control_plane import session_schema
    from control_plane.session_store import _DDL

    made = {m.group(1) for s in _DDL for m in [re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", s)] if m}
    assert set(session_schema.TABLES) == made - {session_schema.VERSION_TABLE}


# ── columns a table gained after its first release (#1783) ───────────────────────────────────────
#: The notification table shipped without `source` (the agent's notify) and `acted` (what the person
#: did with a notice). CREATE TABLE IF NOT EXISTS leaves an existing table as it was, so a deployment
#: that already had the table must be given the columns, or every read of it fails.
_OLD_NOTIFICATION = """CREATE TABLE agent_session_notification (
    id TEXT PRIMARY KEY, recipient TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL,
    actor_name TEXT NOT NULL DEFAULT '', object_kind TEXT NOT NULL, object_id TEXT NOT NULL,
    object_name TEXT NOT NULL DEFAULT '', url TEXT, text TEXT NOT NULL,
    created DOUBLE PRECISION NOT NULL, read_at DOUBLE PRECISION)"""


def test_init_adds_the_columns_an_existing_table_lacks():
    c = _pg()
    session_schema.init(c, role="vexa_agent_sessions", password=PW, database="vexa")
    sql = [s for s, _ in c.log]
    for col in ("source", "acted"):
        assert f"ALTER TABLE agent_session_notification ADD COLUMN IF NOT EXISTS {col} TEXT" in sql
    # after the tables are made, before the grants
    first_alter = next(i for i, s in enumerate(sql) if s.startswith("ALTER TABLE"))
    assert first_alter > max(i for i, s in enumerate(sql) if s.startswith("CREATE TABLE"))


def test_a_sqlite_store_made_before_the_columns_gains_them(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(_OLD_NOTIFICATION)
    con.commit()
    con.close()
    store = SessionStore.open(f"sqlite:///{path}", MemBlobs())
    try:
        cols = {r[1] for r in store._x("PRAGMA table_info(agent_session_notification)").fetchall()}
        assert {"source", "acted"} <= cols
    finally:
        store.close()
