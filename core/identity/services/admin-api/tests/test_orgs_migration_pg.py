"""MIGRATION-0008 — the org as a first-class object is forward-safe on a live database (docker).

What is pinned here is the operator's question, "can this run against production while people sign
in?":

- an install that predates orgs converges to: the default org exists, every account is in it;
- `users` is NOT rewritten (its relfilenode is unchanged) and no per-row UPDATE runs: the column
  arrives with a constant default, which Postgres >= 11 keeps in the catalog;
- a reader of `users` never waits behind the migration for longer than the lock timeout;
- re-running is a no-op; the CRM binding is stamped once and never overwritten;
- the documented rollback (MIGRATION-0008 doc) leaves a database old code serves, and the next
  convergence re-applies cleanly.
"""
import threading
import time

from sqlalchemy import create_engine, inspect, text

from admin_api.schema.models import Base, DEFAULT_ORG_ID
from admin_api.schema.sync import ensure_schema_sync
from conftest import requires_docker

pytestmark = requires_docker
HOLD_S = 5.0

#: The rollback, verbatim from schema/MIGRATION-0008-orgs.md § Rollback.
ROLLBACK = (
    "DROP TABLE IF EXISTS org_members",
    "DROP TABLE IF EXISTS orgs",
    "ALTER TABLE users DROP COLUMN IF EXISTS org_id",
)


def _pre_org_install(engine, n_users=50):
    """A database exactly as the release before orgs left it, holding real accounts."""
    Base.metadata.drop_all(engine)
    ensure_schema_sync(engine, Base)
    with engine.begin() as conn:
        for stmt in ROLLBACK:
            conn.execute(text(stmt))
        for i in range(n_users):
            conn.execute(text("INSERT INTO users (email, name, max_concurrent_bots, data) "
                              "VALUES (:e, :n, 3, '{}'::jsonb)"), {"e": f"p{i}@example.test", "n": f"P{i}"})


def _relfilenode(conn, table):
    return conn.execute(text("SELECT relfilenode FROM pg_class WHERE relname = :t"), {"t": table}).scalar()


def test_a_fresh_install_has_the_default_org(pg_url, monkeypatch):
    monkeypatch.delenv("CRM_ORG_ID", raising=False)
    engine = create_engine(pg_url)
    Base.metadata.drop_all(engine)
    ensure_schema_sync(engine, Base)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id, policy_source, crm_org_id, billing_account FROM orgs")).all()
    assert row == [(DEFAULT_ORG_ID, "_global", None, None)]
    engine.dispose()


def test_an_existing_install_converges_without_rewriting_users(pg_url, monkeypatch):
    monkeypatch.delenv("CRM_ORG_ID", raising=False)
    engine = create_engine(pg_url)
    _pre_org_install(engine)
    with engine.connect() as conn:
        before = _relfilenode(conn, "users")
        xmins_before = conn.execute(text("SELECT array_agg(xmin::text ORDER BY id) FROM users")).scalar()

    ensure_schema_sync(engine, Base)

    with engine.connect() as conn:
        assert _relfilenode(conn, "users") == before, "users was rewritten"
        # No per-row UPDATE: every row is the same tuple version it was before the migration.
        assert conn.execute(text("SELECT array_agg(xmin::text ORDER BY id) FROM users")).scalar() == xmins_before
        orgs = conn.execute(text("SELECT DISTINCT org_id FROM users")).scalars().all()
        assert orgs == [DEFAULT_ORG_ID]
        assert conn.execute(text("SELECT count(*) FROM orgs WHERE id = :d"), {"d": DEFAULT_ORG_ID}).scalar() == 1
        # No membership row per user: home-org membership is derived (app/orgs.py).
        assert conn.execute(text("SELECT count(*) FROM org_members")).scalar() == 0
        # A row old code inserts (no org_id named) lands in the default org.
        conn.execute(text("INSERT INTO users (email, max_concurrent_bots, data) VALUES ('new@example.test', 3, '{}'::jsonb)"))
        assert conn.execute(text("SELECT org_id FROM users WHERE email = 'new@example.test'")).scalar() == DEFAULT_ORG_ID
        conn.rollback()
    engine.dispose()


def test_a_reader_of_users_never_queues_behind_the_migration(pg_url, monkeypatch):
    monkeypatch.delenv("CRM_ORG_ID", raising=False)
    engine = create_engine(pg_url)
    _pre_org_install(engine)
    held = threading.Event()

    def hold():                                       # a long transaction that has read users
        with engine.connect() as conn:
            tx = conn.begin()
            conn.execute(text("SELECT 1 FROM users LIMIT 1"))
            held.set()
            time.sleep(HOLD_S)
            tx.rollback()

    done = {}

    def converge():
        ensure_schema_sync(engine, Base)
        done["ok"] = True

    h = threading.Thread(target=hold); h.start(); assert held.wait(10)
    c = threading.Thread(target=converge); c.start()
    time.sleep(1.0)
    t0 = time.monotonic()
    with engine.connect() as conn:
        conn.execute(text("SELECT count(*) FROM users")).scalar()
    reader = time.monotonic() - t0
    h.join(); c.join(60)
    assert reader < 1.5, f"a reader waited {reader:.1f}s behind the migration"
    assert done.get("ok")
    assert "org_id" in {col["name"] for col in inspect(engine).get_columns("users")}
    engine.dispose()


def test_it_is_idempotent_and_binds_the_crm_org_once(pg_url, monkeypatch):
    engine = create_engine(pg_url)
    monkeypatch.delenv("CRM_ORG_ID", raising=False)
    _pre_org_install(engine)
    ensure_schema_sync(engine, Base)
    monkeypatch.setenv("CRM_ORG_ID", "acme-crm")
    ensure_schema_sync(engine, Base)                    # stamps the unset binding
    with engine.begin() as conn:
        assert conn.execute(text("SELECT crm_org_id FROM orgs WHERE id = :d"), {"d": DEFAULT_ORG_ID}).scalar() == "acme-crm"
        conn.execute(text("UPDATE orgs SET crm_org_id = 'chosen-by-operator' WHERE id = :d"), {"d": DEFAULT_ORG_ID})
    ensure_schema_sync(engine, Base)                    # never overwrites a set binding
    with engine.connect() as conn:
        assert conn.execute(text("SELECT crm_org_id FROM orgs")).scalars().all() == ["chosen-by-operator"]
    engine.dispose()


def test_the_documented_rollback_leaves_a_database_old_code_serves(pg_url, monkeypatch):
    monkeypatch.delenv("CRM_ORG_ID", raising=False)
    engine = create_engine(pg_url)
    _pre_org_install(engine, n_users=3)
    ensure_schema_sync(engine, Base)
    with engine.begin() as conn:
        for stmt in ROLLBACK:
            conn.execute(text(stmt))
    tables = set(inspect(engine).get_table_names())
    assert "orgs" not in tables and "org_members" not in tables
    assert "org_id" not in {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.connect() as conn:                       # nothing lost: the accounts are all there
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 3
    ensure_schema_sync(engine, Base)                    # and a later roll-forward re-applies
    with engine.connect() as conn:
        assert conn.execute(text("SELECT DISTINCT org_id FROM users")).scalars().all() == [DEFAULT_ORG_ID]
    engine.dispose()
