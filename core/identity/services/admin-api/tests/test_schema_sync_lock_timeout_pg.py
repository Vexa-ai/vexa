"""admin-api's startup convergence never queues live reads behind a lock it is waiting for (docker).

A waiting `ALTER TABLE` holds its place in the table's lock queue, and every later reader — every
`/internal/validate` — waits behind it. With no `lock_timeout`, one open transaction on `api_tokens`
while a new admin-api converged its schema would stall every sign-in until that transaction ended.
Now the convergence takes each lock with a short `lock_timeout` and retries with backoff: a reader
arriving meanwhile waits at most that timeout, and the column is still added once the holder is gone."""
import threading
import time

from sqlalchemy import create_engine, inspect, text

from admin_api.schema import sync as sync_mod
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync
from conftest import requires_docker

pytestmark = requires_docker
HOLD_S = 6.0


def test_a_waiting_migration_does_not_stall_readers_and_still_lands(pg_url):
    engine = create_engine(pg_url)
    Base.metadata.drop_all(engine)
    ensure_schema_sync(engine, Base)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE api_tokens DROP COLUMN last_used_at'))

    held = threading.Event()

    def hold():
        with engine.connect() as conn:
            tx = conn.begin()
            conn.execute(text("SELECT 1 FROM api_tokens LIMIT 1"))
            held.set()
            time.sleep(HOLD_S)
            tx.rollback()

    done = {}

    def converge():
        t0 = time.monotonic()
        ensure_schema_sync(engine, Base)
        done["took"] = time.monotonic() - t0

    h = threading.Thread(target=hold); h.start(); assert held.wait(10)
    c = threading.Thread(target=converge); c.start()
    time.sleep(1.0)                                   # the convergence is now waiting for its lock
    t0 = time.monotonic()
    with engine.connect() as conn:
        conn.execute(text("SELECT count(*) FROM api_tokens")).scalar()
    reader = time.monotonic() - t0
    h.join(); c.join(60)
    assert reader < 1.5, f"a reader waited {reader:.1f}s behind the migration's lock"
    assert "last_used_at" in {col["name"] for col in inspect(engine).get_columns("api_tokens")}
    assert sync_mod.LOCK_TIMEOUT_MS <= 1000
    engine.dispose()
