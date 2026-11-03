"""A sensor kind runs only in the scopes it declares, and a person's sensor speaks only for its owner.

  1. `SensorKind.scopes` names where a kind may run; the default is every scope.
  2. The shared-mailbox kind runs in the instance scope only: making, changing, testing or ticking
     one in a person's scope is refused `kind_not_allowed_in_scope` (403), and the mailbox is never
     opened for it.
  3. A change from a person's sensor that names a different person (`refs.uid`) is dropped with its
     reason, never re-stamped with the owner.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flows_sensors import Change, SensorKind, Tick, pool, store  # noqa: E402
from flows_sensors.base import SensorRow  # noqa: E402
from flows_sensors.calendar import KINDS, CalendarSensor  # noqa: E402
from flows_sensors.mailbox import MailboxSensor  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

NOW = 1_900_000_000.0


class _Spy:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("the mailbox was opened")


def _kinds(spy):
    return {"calendar": CalendarSensor(call=lambda *a, **k: {"items": [], "cursor": "c"}),
            "mailbox": MailboxSensor(inbox_factory=spy, db_factory=lambda: None,
                                     known_uid=lambda e: None, rules=lambda: {}, clock=lambda: NOW)}


def _legacy_row(db, scope="user:7", name="inbox"):
    """A row made before kinds declared their scopes."""
    db.execute("INSERT INTO sensor (scope, name, kind, interval_s, version, tested_version, status, "
               "mode, next_due_at, failures, created_at) VALUES (:sc, :n, 'mailbox', 60, 1, 1, "
               "'active', 'poll', 0, 0, 0)", {"sc": scope, "n": name})


def test_kinds_declare_their_scopes():
    assert SensorKind.scopes == ("user", "instance")
    assert KINDS["calendar"].scopes == ("user", "instance")
    assert KINDS["mailbox"].scopes == ("instance",)


def test_a_person_cannot_make_a_mailbox_sensor_in_their_own_scope():
    db, spy = SqliteDB(), _Spy()
    with pytest.raises(store.SensorError) as e:
        store.create(db, _kinds(spy), "user:7", name="inbox", kind="mailbox")
    assert e.value.reason == "kind_not_allowed_in_scope" and e.value.code == 403
    assert store.get(db, "user:7", "inbox") is None and spy.calls == 0


def test_the_instance_scope_may_still_make_a_mailbox_sensor():
    db = SqliteDB()
    s = store.create(db, _kinds(_Spy()), "instance", name="info-invites", kind="mailbox")
    assert s.kind == "mailbox" and s.scope == "instance"


def test_a_mailbox_row_in_a_persons_scope_is_never_tested_changed_or_ticked():
    db, spy = SqliteDB(), _Spy()
    kinds = _kinds(spy)
    _legacy_row(db)
    with pytest.raises(store.SensorError) as e:
        store.test(db, kinds, "user:7", "inbox")
    assert e.value.reason == "kind_not_allowed_in_scope" and e.value.code == 403
    with pytest.raises(store.SensorError) as e:
        store.update(db, kinds, "user:7", "inbox", interval_s=120)
    assert e.value.reason == "kind_not_allowed_in_scope"
    row = store.get(db, "user:7", "inbox")
    assert pool.dry_run(kinds["mailbox"], row)["reason"] == "kind_not_allowed_in_scope"
    out = pool.run_due(db, kinds, lambda *a: 1, NOW)
    assert out and out[0]["failed"] == "kind_not_allowed_in_scope" and out[0]["status"] == "stopped"
    assert spy.calls == 0, "the shared mailbox was never opened for a person's sensor"


class _Speaks(SensorKind):
    kind = "speaks"
    event_types = ("mail.reply",)

    def tick(self, sensor, cursor):
        return Tick(changes=[Change("mail.reply", "m-1", {"uid": "9", "text": "hi"}),
                             Change("mail.reply", "m-2", {"uid": "7", "text": "mine"}),
                             Change("mail.reply", "m-3", {"text": "unnamed"})], cursor="c")


def test_a_persons_sensor_never_speaks_for_another_person():
    s = SensorRow(scope="user:7", name="s", kind="speaks", status="active")
    kept, dropped = pool.sort_changes(_Speaks(), s, _Speaks().tick(s, None).changes)
    assert [c.dedupe_key for c in kept] == ["m-2", "m-3"]
    assert all(c.refs["uid"] == "7" for c in kept)
    assert dropped == [{"event_type": "mail.reply", "key": "m-1", "reason": "names_another_person"}]


def test_an_instance_sensor_keeps_the_person_its_change_names():
    s = SensorRow(scope="instance", name="s", kind="speaks", status="active")
    kept, dropped = pool.sort_changes(_Speaks(), s, _Speaks().tick(s, None).changes)
    assert [c.refs.get("uid") for c in kept] == ["9", "7", None] and dropped == []
