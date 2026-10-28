"""The state backup (`credential_broker.backup`): a consistent snapshot while the broker writes,
retention that keeps the newest N, and no key read or copied."""
import sqlite3
import sys
import threading
from contextlib import closing

import pytest

from credential_broker import backup
from credential_broker.broker import Broker
from credential_broker.store import LocalEncryptedStore

KEY = bytes(range(32))


def _state(settings, store):
    broker = Broker(settings, store)
    local = LocalEncryptedStore(settings.state_dir / "secrets.sqlite", KEY)
    return broker, local


def test_a_snapshot_restores_into_a_working_store(settings, store, tmp_path):
    broker, local = _state(settings, store)
    local.put("abc", {"value": "one"})
    broker.sql("CREATE TABLE IF NOT EXISTS marker (v TEXT)")
    broker.sql("INSERT INTO marker VALUES ('kept')")
    snap = backup.snapshot(settings.state_dir, tmp_path / "backup", now=0)
    assert snap.name == "snapshot-19700101T000000Z"
    assert sorted(p.name for p in snap.iterdir()) == ["metadata.sqlite", "secrets.sqlite"]
    # one self-contained file each, readable without the live volume, and decryptable with the key
    for name in ("metadata.sqlite", "secrets.sqlite"):
        with closing(sqlite3.connect(snap / name)) as c:
            assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert LocalEncryptedStore(snap / "secrets.sqlite", KEY).get("abc").data == {"value": "one"}
    with closing(sqlite3.connect(snap / "metadata.sqlite")) as c:
        assert c.execute("SELECT v FROM marker").fetchone()[0] == "kept"


def test_a_snapshot_taken_during_writes_is_one_point_in_time(settings, store, tmp_path):
    """Snapshots taken while a writer keeps putting new versions are each intact (integrity_check)
    and every row in them decrypts: no page from a half-applied write."""
    broker, local = _state(settings, store)
    stop = threading.Event()
    errors = []

    def write():
        n = 0
        try:
            while not stop.is_set():
                n += 1
                local.put(f"p{n % 3}", {"n": n})
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    t = threading.Thread(target=write)
    t.start()
    try:
        snaps = [backup.snapshot(settings.state_dir, tmp_path / "backup", now=i) for i in range(5)]
    finally:
        stop.set()
        t.join()
    assert errors == []
    for snap in snaps:
        with closing(sqlite3.connect(snap / "secrets.sqlite")) as c:
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            rows = c.execute("SELECT path, version FROM secret_versions").fetchall()
        reopened = LocalEncryptedStore(snap / "secrets.sqlite", KEY)
        for path, version in rows:                         # every row decrypts: no torn page
            assert reopened.get(path, version=version) is not None


def test_retention_keeps_the_newest_n_and_clears_a_partial(settings, store, tmp_path):
    _state(settings, store)
    dest = tmp_path / "backup"
    for i in range(6):
        backup.snapshot(settings.state_dir, dest, now=i * 3600)
    (dest / "snapshot-20990101T000000Z.partial").mkdir()
    (dest / "operator-notes").mkdir()                     # not ours: left alone
    removed = backup.prune(dest, 3)
    assert sorted(removed) == ["snapshot-19700101T000000Z", "snapshot-19700101T010000Z",
                               "snapshot-19700101T020000Z"]
    assert sorted(p.name for p in dest.iterdir()) == [
        "operator-notes", "snapshot-19700101T030000Z", "snapshot-19700101T040000Z",
        "snapshot-19700101T050000Z"]


def test_an_empty_state_volume_writes_nothing(tmp_path):
    (tmp_path / "state").mkdir()
    assert backup.snapshot(tmp_path / "state", tmp_path / "backup") is None
    assert not (tmp_path / "backup").exists()


def test_no_key_is_read_or_copied(settings, store, tmp_path):
    """The state volume holds the broker's HMAC key, and a store key lies beside it here (as a
    misconfigured deployment might put it). The backup opens only the two databases."""
    broker, local = _state(settings, store)
    local.put("abc", {"value": "one"})
    store_key = settings.state_dir / "store.key"
    store_key.write_text(KEY.hex())
    opened: list = []
    watching = [True]

    def hook(event, args):
        if watching[0] and event in ("open", "sqlite3.connect", "shutil.copyfile", "os.listdir",
                                     "os.scandir"):
            opened.append((event, str(args[0]) if args else ""))

    sys.addaudithook(hook)
    try:
        snap = backup.snapshot(settings.state_dir, tmp_path / "backup", now=0)
        backup.prune(tmp_path / "backup", 1)
    finally:
        watching[0] = False
    touched = " ".join(p for _, p in opened)
    assert "store.key" not in touched and "state-hmac.key" not in touched, opened
    connected = {p for e, p in opened if e == "sqlite3.connect"}
    assert {c.split("?")[0].removeprefix("file:") for c in connected if "state" in c} == {
        str(settings.state_dir / "metadata.sqlite"), str(settings.state_dir / "secrets.sqlite")}
    assert sorted(p.name for p in snap.iterdir()) == ["metadata.sqlite", "secrets.sqlite"]
    blob = b"".join(p.read_bytes() for p in snap.iterdir())
    assert KEY.hex().encode() not in blob and KEY not in blob


def test_the_cli_refuses_a_destination_inside_the_state_volume(tmp_path):
    with pytest.raises(SystemExit):
        backup.main(["--source", str(tmp_path), "--dest", str(tmp_path / "b"), "--once"])


def test_the_loop_survives_a_failed_snapshot(tmp_path, monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("disk full")
    monkeypatch.setattr(backup, "snapshot", boom)

    class Stop(Exception):
        pass

    def sleep(_s):
        if len(calls) >= 2:
            raise Stop
    with pytest.raises(Stop):
        backup.main(["--source", str(tmp_path / "s"), "--dest", str(tmp_path / "b"),
                     "--interval", "60"], sleep=sleep)
    assert len(calls) == 2                       # a failure was logged, and the next interval ran
    assert backup.main(["--source", str(tmp_path / "s"), "--dest", str(tmp_path / "b"), "--once"]) == 1
