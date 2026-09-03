"""The INBOUND DOUBLE — mailpit as a second inbox source, driven from recorded fixtures.

The dev stack's mail double is mailpit: REST only, no IMAP and no POP3, so the Gmail-hardcoded
poller could not receive anything there and an invite or a reply had to be injected as a fact.
These tests hold the four properties that make the second source usable as a rehearsal surface:

  1. an ICS invite (Zoom link, two ATTENDEEs) becomes exactly the `invite.received` refs
  2. a reply carrying In-Reply-To routes by THREAD into `mail.reply`
  3. the cursor never re-admits across a restart, even with the whole history back in the window
  4. the IMAP path is untouched — the same bytes through either source yield identical facts

`tests/mailpit/messages.json` is the shape mailpit 1.30.7 actually returns (recorded off the rig,
including Go's RFC3339Nano trailing-zero trim: `.5Z` next to `.503Z`).
"""
from __future__ import annotations

import calendar
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import EventType, FakeClock, Registry  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402
from flows_integrations.inbox import (  # noqa: E402
    ImapInbox,
    MailpitInbox,
    from_rfc822,
    get_inbox,
    iso_epoch,
    iso_norm,
)
from flows_integrations.mailbox import handle, parse_ics  # noqa: E402
from flows_steps.emailx import register_thread  # noqa: E402

FIX = Path(__file__).resolve().parent / "mailpit"
SELF = "vexa@example.com"


@pytest.fixture(autouse=True)
def _this_deployment_serves_example(monkeypatch):
    """The fixture world is a deployment that serves `example.test` — so say so.

    The intake now refuses to act for an address that is neither a known user nor inside the
    deployment's domain allow-list (R-B12), and the recorded corpus is a `example.test` organizer and
    a `example.test` attendee writing to a mailbox at `example.com`. That combination is a real deployment
    shape (we host the mailbox, the customer's people write to it) and it is expressed the way the
    PRD says it must be — as a deployment value, `VEXA_FLOWS_MAIL_DOMAINS`. Without it these mails
    are strangers, which is the correct new answer and not what this file is about."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_DOMAINS", "example.test")
BEFORE_ALL = "2026-09-01T21:00:00.000000Z"
EML = {"1InvitePlatformSyncZZZ": "invite-platform-sync.eml",
       "2StrangerYYYYYYYYYYYYY": "not-for-us.eml",
       "3ReplyCaseyXXXXXXXXXXX": "reply-minutes.eml"}


def recorded_mailpit(path: str) -> bytes:
    """The recorded mailpit API: the list endpoint (paged) and the raw-source endpoint."""
    u = urlparse(path)
    if u.path == "/api/v1/messages":
        q = parse_qs(u.query)
        start = int(q.get("start", ["0"])[0])
        limit = int(q.get("limit", ["50"])[0])
        doc = json.loads((FIX / "messages.json").read_text())
        doc["messages"] = doc["messages"][start:start + limit]
        doc["count"] = len(doc["messages"])
        return json.dumps(doc).encode()
    if u.path.startswith("/api/v1/message/") and u.path.endswith("/raw"):
        return (FIX / EML[u.path.split("/")[4]]).read_bytes()
    raise AssertionError(f"mailpit fixture has no recording for {path}")


def rig(lookback_s: float = 86_400.0):
    """A db with both flows registered, and a mailpit inbox positioned before the whole fixture."""
    db, clock, reg = SqliteDB(), FakeClock(), Registry()

    @reg.step
    def noop(ctx):
        from flows import Done
        return Done()

    reg.flow(name="invite_intake", version=1, on=EventType("invite.received"), steps=[noop])
    reg.flow(name="email_chat", version=1, on=EventType("mail.reply"), steps=[noop])

    # the minutes mail that this fixture's reply answers — the thread row is what routes it
    register_thread(db, "<minutes-thread-1@vexa.ai>", "7", "main")

    inbox = MailpitInbox(base_url="http://recorded", addr=SELF, opener=recorded_mailpit,
                         lookback_s=lookback_s)
    assert inbox.restore(db) is None, "a virgin db has no position"
    inbox.anchor(db, BEFORE_ALL)
    return db, reg, clock, inbox


def drive(db, reg, clock, inbox, cursor: str, known: dict | None = None):
    """One poll: exactly what mailbox.main()'s loop body does, with the services injected.

    `known` is the account directory. It defaults to Casey — the person the registered thread
    belongs to — because a reply on a thread now runs a turn only for that thread's own
    participant: `In-Reply-To` says WHICH conversation, it never says who the sender is (R-B12)."""
    known = {"casey@example.test": "7"} if known is None else known
    out = []
    for msg in inbox.fetch(cursor):
        out.append((msg, handle(db, reg, clock, SELF, msg,
                                known_uid=lambda e: known.get(e.strip().lower()),
                                is_scaffolded=lambda u: False,
                                provision=lambda e: "99")))
        cursor = msg.cursor
        inbox.commit(db, msg)
    return cursor, out


def refs_of(db, event_type: str) -> list[dict]:
    return [json.loads(r[0]) for r in db.execute(
        "SELECT subject_refs FROM reaction WHERE event_type = :e ORDER BY created_at",
        {"e": event_type})]


# ---------------------------------------------------------------------------------------------
# 1 · the invite
# ---------------------------------------------------------------------------------------------
def test_zoom_invite_becomes_the_exact_invite_received_refs():
    db, reg, clock, inbox = rig()
    _, out = drive(db, reg, clock, inbox, BEFORE_ALL)

    kinds = [o[1][0] for o in out]
    assert kinds == ["invite", "thread_reply"], "oldest first, stranger filtered out"

    ev = refs_of(db, "invite.received")[0]
    assert ev == {
        "organizer": "organizer@example.test",
        "url": "https://us02web.zoom.us/j/84123456789?pwd=aBcD1234efGH",
        "start": float(calendar.timegm(time.strptime("20300302T140000", "%Y%m%dT%H%M%S"))),
        "ics_uid": "platform-sync-20300302@zoom.us",
        # THE OCCURRENCE — what makes this one instance of a series rather than the series
        # (R-B02). `RECURRENCE-ID` when the sender sends one, else the occurrence's own DTSTART.
        "occurrence": "20300302T140000Z",
        "title": "Platform Sync weekly",
        "group": "platform-sync",
        "participants": ["casey@example.test", "priya@example.test"],
        # the ATTENDEE lines' own CN= display names, address -> name. Without them, matching a
        # transcript speaker to somebody on the invite means guessing a name out of an email local
        # part, which is the guess the room ordering must not make.
        "participant_names": {"casey@example.test": "Casey Lund", "priya@example.test": "Priya Raman"},
    }


def test_participants_exclude_us_case_insensitively_and_organizer_is_unchanged():
    ics = (FIX / "invite-platform-sync.eml").read_text().split("BEGIN:VCALENDAR", 1)[1]
    ev = parse_ics("BEGIN:VCALENDAR" + ics, "VeXa@ExAmple.com")
    assert SELF not in ev["participants"] and ev["participants"] == ["casey@example.test",
                                                                    "priya@example.test"]
    assert ev["organizer"] == "organizer@example.test"        # exactly as before this change


def test_meet_invites_still_parse_and_now_carry_participants():
    meet = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:u-1\nDTSTART:20300302T140000Z\n"
            "ORGANIZER:mailto:anna@bank.com\nATTENDEE:mailto:ben@bank.com\n"
            "ATTENDEE:mailto:vexa@example.com\nSUMMARY:Pilot sync\n"
            "LOCATION:https://meet.google.com/jrn-qwko-mqp\nEND:VEVENT\nEND:VCALENDAR\n")
    ev = parse_ics(meet, SELF)
    assert ev["url"] == "https://meet.google.com/jrn-qwko-mqp"
    assert ev["participants"] == ["ben@bank.com"]
    assert ev["group"] is None and ev["title"] == "Pilot sync"


# ---------------------------------------------------------------------------------------------
# 2 · the reply
# ---------------------------------------------------------------------------------------------
def test_reply_routes_by_thread_not_by_sender():
    db, reg, clock, inbox = rig()
    drive(db, reg, clock, inbox, BEFORE_ALL, known={"casey@example.test": "7"})

    r = refs_of(db, "mail.reply")[0]
    assert r["uid"] == "7" and r["session"] == "main", "the thread row decides, never the sender"
    assert r["from_addr"] == "casey@example.test"
    assert r["orig_msgid"] == "<reply-casey-1@example.test>"
    assert r["text"].strip() == "Point 3 is wrong - the vote was deferred, not carried."
    assert ">" not in r["text"], "quoted history is stripped"


def test_mail_addressed_to_another_tenant_is_never_fetched():
    db, reg, clock, inbox = rig()
    _, out = drive(db, reg, clock, inbox, BEFORE_ALL)
    assert all(m.frm != "someone@rehearsal.test" for m, _ in out)
    assert "2StrangerYYYYYYYYYYYYY" not in {r[0] for r in db.execute(
        "SELECT ext_id FROM mail_seen")}


# ---------------------------------------------------------------------------------------------
# 3 · the cursor
# ---------------------------------------------------------------------------------------------
def test_cursor_never_re_admits_across_a_restart():
    db, reg, clock, inbox = rig()
    cursor, out = drive(db, reg, clock, inbox, BEFORE_ALL)
    assert len(out) == 2
    before = db.execute("SELECT COUNT(*) FROM reaction")[0][0]

    # the process dies. A NEW inbox object (empty in-memory seen set) on the SAME database, with
    # a re-scan window wide enough that the watermark alone cannot suppress anything: only the
    # persisted seen set can, which is the property under test.
    restarted = MailpitInbox(base_url="http://recorded", addr=SELF, opener=recorded_mailpit,
                             lookback_s=86_400.0)
    resumed = restarted.restore(db)
    assert resumed == cursor, "the position survived the restart"
    assert iso_epoch(resumed) > iso_epoch(BEFORE_ALL)

    cursor2, out2 = drive(db, reg, clock, restarted, resumed)
    assert out2 == [], "a restart re-admitted mail it had already routed"
    assert db.execute("SELECT COUNT(*) FROM reaction")[0][0] == before


def test_first_boot_anchors_at_the_tail_and_never_replays_history():
    db, reg, clock = SqliteDB(), FakeClock(), Registry()
    inbox = MailpitInbox(base_url="http://recorded", addr=SELF, opener=recorded_mailpit)
    assert inbox.restore(db) is None
    tail = inbox.tail_cursor()
    assert tail == iso_norm("2026-09-01T21:20:11.5Z"), "the newest message IS the tail"
    inbox.anchor(db, tail)
    assert list(inbox.fetch(tail)) == [], "the double's whole rehearsal history was replayed"


def test_go_trimmed_fractions_order_correctly():
    # `.5Z` sorts AFTER `.503Z` as a string and BEFORE it in time — mailpit emits both shapes,
    # so every comparison goes through epochs and every stored watermark is fixed width.
    assert "2026-09-01T21:14:02.5Z" > "2026-09-01T21:14:02.503Z"
    assert iso_epoch("2026-09-01T21:14:02.5Z") < iso_epoch("2026-09-01T21:14:02.503Z")
    assert iso_norm("2026-09-01T21:14:02.5Z") < iso_norm("2026-09-01T21:14:02.503Z")
    assert iso_norm("2026-09-01T21:14:02Z") == "2026-09-01T21:14:02.000000Z"
    assert iso_epoch("2026-09-01T23:14:02+02:00") == iso_epoch("2026-09-01T21:14:02Z")


# ---------------------------------------------------------------------------------------------
# 4 · the IMAP path
# ---------------------------------------------------------------------------------------------
def test_the_two_sources_yield_identical_facts_from_identical_bytes():
    raw = (FIX / "invite-platform-sync.eml").read_bytes()
    as_imap = from_rfc822(raw, cursor="4711", ext_id="4711")        # an IMAP UID
    as_mailpit = from_rfc822(raw, cursor=iso_norm("2026-09-01T21:14:02.503Z"),
                             ext_id="1InvitePlatformSyncZZZ")
    for f in ("message_id", "frm", "subject", "headers", "body", "ics"):
        assert getattr(as_imap, f) == getattr(as_mailpit, f), f
    assert parse_ics(as_imap.ics, SELF) == parse_ics(as_mailpit.ics, SELF)


def test_imap_stays_the_default_and_still_points_at_gmail(monkeypatch):
    monkeypatch.delenv("VEXA_MAIL_INBOX", raising=False)
    box = get_inbox()
    assert isinstance(box, ImapInbox) and box.name == "imap"
    assert box.host == "imap.gmail.com" and box.folder == "INBOX"

    monkeypatch.setenv("VEXA_MAIL_INBOX", "mailpit")
    monkeypatch.setenv("VEXA_MAIL_ADDR", SELF)
    monkeypatch.setenv("VEXA_MAILPIT_URL", "http://127.0.0.1:8025")
    assert get_inbox().name == "mailpit"

    monkeypatch.setenv("VEXA_MAIL_INBOX", "pigeon")
    try:
        get_inbox()
        raise AssertionError("an unknown inbox name must fail loudly at boot")
    except ValueError:
        pass


def test_imap_cursor_row_is_the_integer_it_always_was():
    db = SqliteDB()
    box = ImapInbox()
    assert box.restore(db) is None
    box.anchor(db, "4711")
    assert box.restore(db) == "4711"
    box.commit(db, from_rfc822((FIX / "reply-minutes.eml").read_bytes(),
                               cursor="4712", ext_id="4712"))
    assert db.execute("SELECT uid, token FROM mail_cursor WHERE id = 1")[0] == (4712, None)
    assert db.execute("SELECT COUNT(*) FROM mail_seen")[0][0] == 0, "IMAP writes no seen rows"
