"""THE `mailbox` SENSOR KIND — a shared mailbox (`info@`) read through the common sensor pool.

  1. First read / `sensor_test`: only invitations to meetings that have not started are kept; a
     reply from before the sensor existed and every other message are dropped; the cursor anchors.
  2. An invite carries the contract `invite_join` follows (organizer, url, start, event_key,
     sequence, title, participants, workspace) and this deployment's workspace link, deduped by
     UID+sequence.
  3. A cancellation is `invite.cancelled` {event_key}.
  4. A reply to a Vexa thread from that thread's person is `mail.reply`; a stranger's mail is dropped.
  5. An organizer nobody can place emits nothing; a LIVE sensor records the quarantine, a dry run
     writes nothing at all.
  6. Registered beside `calendar`, so `sensor_create kind=mailbox` is the common tool.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flows_integrations.inbox import InboundMessage  # noqa: E402
from flows_sensors import pool  # noqa: E402
from flows_sensors.base import SensorRow  # noqa: E402
from flows_sensors.mailbox import MailboxSensor  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402
from mail_auth_fixture import authed  # noqa: E402

NOW = 1_900_000_000.0          # 2030-03-17
SELF = "info@vexa.ai"


def _ics(uid="evt-1", start="20300320T100000Z", organizer="host@vexa.ai", method="REQUEST",
         seq=0, extra=""):
    return ("BEGIN:VCALENDAR\r\nMETHOD:" + method + "\r\nBEGIN:VEVENT\r\nUID:" + uid + "\r\n"
            f"SEQUENCE:{seq}\r\nDTSTART:{start}\r\nSUMMARY:Collab kickoff\r\n"
            f"ORGANIZER;CN=Host:mailto:{organizer}\r\n"
            f"ATTENDEE;CN=Anna:mailto:anna@partner.com\r\nATTENDEE:mailto:{SELF}\r\n"
            "DESCRIPTION:Join: https://meet.google.com/abc-defg-hij\\nWorkspace: "
            "https://app.dev.vexa.ai/join/Tok3n_abc-9" + extra + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def _msg(n, frm="host@vexa.ai", ics=None, headers=None, subject="Invitation"):
    return InboundMessage(cursor=str(n), ext_id=str(n), message_id=f"<in-{n}@mail>", frm=frm,
                          subject=subject, headers=authed(frm, headers), body="text", ics=ics)


class _Inbox:
    name = "fake"

    def __init__(self, msgs):
        self.msgs = list(msgs)

    def address(self):
        return SELF

    def recent(self, n):
        return self.msgs[-n:]

    def tail_cursor(self):
        return self.msgs[-1].cursor if self.msgs else "0"

    def fetch(self, cursor):
        return [m for m in self.msgs if int(m.cursor) > int(cursor)]


def _kind(msgs, db, users=None, rules=None):
    users = users or {"host@vexa.ai": "7", "dima@vexa.ai": "8"}
    return MailboxSensor(inbox_factory=lambda: _Inbox(msgs), db_factory=lambda: db,
                         known_uid=lambda e: users.get(e), rules=lambda: rules or {},
                         clock=lambda: NOW)


def _row(status="active"):
    return SensorRow(scope="instance", name="info-invites", kind="mailbox", status=status)


def test_first_read_keeps_only_future_invitations_and_anchors(monkeypatch):
    monkeypatch.setenv("VEXA_UI_URL", "https://app.dev.vexa.ai")
    db = SqliteDB()
    db.execute("INSERT INTO mail_thread (message_id, subject_uid, session, created_at) "
               "VALUES ('<vexa-1@vexa.ai>', '8', 'main', 0)")
    msgs = [_msg(1, frm="customer@shop.com", subject="Order 42"),
            _msg(2, ics=_ics(uid="old", start="20300101T100000Z")),
            _msg(3, frm="dima@vexa.ai", headers={"In-Reply-To": "<vexa-1@vexa.ai>"}),
            _msg(4, ics=_ics())]
    tick = _kind(msgs, db).tick(_row(), None)
    assert tick.cursor == "4"
    assert [c.event_type for c in tick.changes] == ["invite.received"]
    refs = tick.changes[0].refs
    assert {k: refs[k] for k in ("organizer", "url", "event_key", "sequence", "title")} == {
        "organizer": "host@vexa.ai", "url": "https://meet.google.com/abc-defg-hij",
        "event_key": "evt-1", "sequence": 0, "title": "Collab kickoff"}
    assert refs["start"] > NOW and "anna@partner.com" in refs["participants"]
    assert refs["workspace_invite_token"] == "Tok3n_abc-9"
    assert tick.changes[0].dedupe_key == "host@vexa.ai/evt-1:0"


def test_incremental_reads_replies_cancellations_and_reschedules():
    db = SqliteDB()
    db.execute("INSERT INTO mail_thread (message_id, subject_uid, session, created_at) "
               "VALUES ('<vexa-1@vexa.ai>', '8', 'main', 0)")
    msgs = [_msg(5, frm="dima@vexa.ai", headers={"In-Reply-To": "<vexa-1@vexa.ai>"}),
            _msg(6, frm="stranger@else.com", headers={"In-Reply-To": "<vexa-1@vexa.ai>"}),
            _msg(7, ics=_ics(seq=1)),
            _msg(8, ics=_ics(method="CANCEL", seq=2)),
            _msg(9, frm="customer@shop.com")]
    tick = _kind(msgs, db).tick(_row(), "4")
    kinds = [(c.event_type, c.dedupe_key) for c in tick.changes]
    assert kinds == [("mail.reply", "mail-<in-5@mail>"), ("invite.received", "host@vexa.ai/evt-1:1"),
                     ("invite.cancelled", "evt-1:cancel:2")]
    assert tick.changes[0].refs["uid"] == "8" and tick.cursor == "9"


def test_two_organizers_invites_with_one_uid_are_two_changes():
    """One UID is the meeting's in every invitee's calendar (Vexa-ai/vexa#1996 review): the change
    id names whose invite it is, so the second is not deduped into the first one's intake run."""
    db = SqliteDB()
    msgs = [_msg(7, ics=_ics(seq=1)),
            _msg(8, frm="dima@vexa.ai", ics=_ics(seq=1, organizer="dima@vexa.ai")),
            _msg(9, ics=_ics(seq=1))]                       # the first one again
    keys = [c.dedupe_key for c in _kind(msgs, db).tick(_row(), "4").changes]
    assert keys == ["host@vexa.ai/evt-1:1", "dima@vexa.ai/evt-1:1", "host@vexa.ai/evt-1:1"]


def test_a_resync_after_the_upgrade_dedups_into_the_intake_admitted_under_the_old_key():
    """A run admitted under the key before the owner was in it (`<event_key>:<sequence>`) is its
    organizer's: the first read after the upgrade dedups into it (no second RSVP, ack or prep),
    and another organizer's invite with that UID still gets its own run."""
    import json

    from flows import FakeClock, Registry, admit
    from flows_defs import production
    db, reg, clock = SqliteDB(), Registry(), FakeClock(NOW)
    production.build(reg, db)
    reg.refresh_from_db(db)
    admit(db, reg, clock, source_event_id="sensor:instance/info-invites:evt-1:0",
          event_type="invite.received",
          subject_refs={"organizer": "host@vexa.ai", "url": "https://meet.google.com/abc-defg-hij",
                        "start": NOW + 86400, "event_key": "evt-1", "sequence": 0})

    def emit(event_type, source_id, refs, earlier=()):
        return admit(db, reg, clock, source_event_id=source_id, event_type=event_type,
                     subject_refs=refs, earlier=earlier)
    msgs = [_msg(1, ics=_ics()), _msg(2, frm="dima@vexa.ai", ics=_ics(organizer="dima@vexa.ai"))]
    pool.tick_one(db, _kind(msgs, db), _row(), emit, NOW)
    intakes = sorted(json.loads(r)["organizer"] for (r,) in db.execute(
        "SELECT subject_refs FROM reaction WHERE flow = 'invite_intake'"))
    assert intakes == ["dima@vexa.ai", "host@vexa.ai"], "one run each; the old one not repeated"


def test_an_organizer_nobody_can_place_emits_nothing_and_only_a_live_sensor_records_it():
    db = SqliteDB()
    msgs = [_msg(1, frm="x@stranger.com", ics=_ics(organizer="x@stranger.com"))]
    assert _kind(msgs, db).tick(_row(status="draft"), "0").changes == []
    assert db.execute("SELECT COUNT(*) FROM mail_quarantine")[0][0] == 0
    assert _kind(msgs, db).tick(_row(status="active"), "0").changes == []
    assert db.execute("SELECT kind FROM mail_quarantine")[0][0] == "unverified_invite"


def test_the_company_switching_invites_off_emits_nothing():
    db = SqliteDB()
    tick = _kind([_msg(1, ics=_ics())], db, rules={"bot_joins_on_invite": False}).tick(_row(), "0")
    assert tick.changes == []


def test_sensor_test_is_the_common_dry_run_and_writes_nothing():
    db = SqliteDB()
    kind = _kind([_msg(1, frm="customer@shop.com"), _msg(2, ics=_ics()),
                  _msg(3, frm="x@stranger.com", ics=_ics(organizer="x@stranger.com"))], db)
    out = pool.dry_run(kind, _row(status="draft"))
    assert out["ok"] and out["counts"]["would_emit"] == 1
    assert out["would_emit"][0]["event_type"] == "invite.received"
    assert db.execute("SELECT COUNT(*) FROM mail_quarantine")[0][0] == 0


def test_the_kind_is_registered_beside_calendar():
    from flows_sensors.calendar import KINDS
    assert {"calendar", "mailbox"} <= set(KINDS)
    assert set(KINDS["mailbox"].event_types) == {"invite.received", "invite.cancelled", "mail.reply"}
