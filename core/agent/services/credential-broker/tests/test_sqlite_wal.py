"""Both broker databases run in WAL mode with a busy timeout: a fresh state volume and one written
by an older broker in rollback-journal mode both end up in WAL, and concurrent writers wait for
each other instead of failing with "database is locked"."""
import sqlite3
import threading
from contextlib import closing

from credential_broker import sqlite_db
from credential_broker.broker import Broker
from credential_broker.store import LocalEncryptedStore

KEY = bytes(range(32))


def _mode(path) -> str:
    with closing(sqlite3.connect(path)) as c:
        return c.execute("PRAGMA journal_mode").fetchone()[0]


def test_a_fresh_state_volume_opens_in_wal(settings, store):
    broker = Broker(settings, store)
    LocalEncryptedStore(settings.state_dir / "secrets.sqlite", KEY)
    assert _mode(broker.db_path) == "wal"
    assert _mode(settings.state_dir / "secrets.sqlite") == "wal"


def test_an_existing_rollback_journal_volume_converts_in_place(settings, store):
    # An older broker's files: default (delete) journal mode, with rows in them.
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    secrets = settings.state_dir / "secrets.sqlite"
    with closing(sqlite3.connect(secrets)) as c:
        c.execute("CREATE TABLE secret_versions (path TEXT NOT NULL, version INTEGER NOT NULL,"
                  " nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, key_id TEXT NOT NULL, created REAL NOT NULL,"
                  " PRIMARY KEY (path, version))")
        c.commit()
    meta = settings.state_dir / "metadata.sqlite"
    with closing(sqlite3.connect(meta)) as c:
        c.execute("CREATE TABLE marker (v TEXT)")
        c.execute("INSERT INTO marker VALUES ('kept')")
        c.commit()
    assert _mode(secrets) == "delete" and _mode(meta) == "delete"

    local = LocalEncryptedStore(secrets, KEY)
    local.put("abc", {"value": "one"})
    broker = Broker(settings, store)
    assert _mode(secrets) == "wal" and _mode(meta) == "wal"
    assert local.get("abc").data == {"value": "one"}
    assert broker.sql("SELECT v FROM marker", one=True) == {"v": "kept"}   # rows survive the switch


def test_every_connection_waits_for_a_busy_writer(tmp_path):
    c = sqlite_db.connect(tmp_path / "x.sqlite")
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == sqlite_db.BUSY_TIMEOUT_MS
    c.close()


def test_concurrent_writers_do_not_see_database_is_locked(settings, store):
    """Two store instances (as two processes would be: separate in-process locks) and the metadata
    database, written from eight threads at once while another thread reads."""
    broker = Broker(settings, store)
    secrets = settings.state_dir / "secrets.sqlite"
    stores = [LocalEncryptedStore(secrets, KEY), LocalEncryptedStore(secrets, KEY)]
    broker.sql("CREATE TABLE IF NOT EXISTS contention (writer INTEGER, n INTEGER)")
    errors: list = []
    stop = threading.Event()

    def write(i: int) -> None:
        try:
            for n in range(25):
                stores[i % 2].put(f"w{i}", {"n": n})
                broker.sql("INSERT INTO contention VALUES (?, ?)", (i, n))
        except Exception as exc:  # noqa: BLE001 — any failure is the finding
            errors.append(repr(exc))

    def read() -> None:
        try:
            while not stop.is_set():
                broker.sql("SELECT COUNT(*) AS n FROM contention", one=True)
                stores[0].get("w0")
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    reader = threading.Thread(target=read)
    reader.start()
    writers = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    reader.join()
    assert errors == []
    assert broker.sql("SELECT COUNT(*) AS n FROM contention", one=True)["n"] == 8 * 25
    assert all(stores[0].get(f"w{i}").data == {"n": 24} for i in range(8))
