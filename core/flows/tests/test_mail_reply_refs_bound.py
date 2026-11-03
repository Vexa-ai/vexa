"""WHAT A `mail.reply` REACTION HOLDS — a declared set of fields, each with a hard size, and nothing else.

A threaded reply becomes a reaction, and the reaction row is durable state (kept until fact retention,
`VEXA_FLOWS_FACT_RETENTION_DAYS`, removes it). So whatever the intake writes into `subject_refs` is
stored mail content. The steps that read it need exactly:

  * `feedback_turn` — `uid`, `session`, the sender's own words (`text`), the sender (`from_addr`);
  * `email_reply`   — `uid`, `session`, `from_addr`, `subject`, `orig_msgid` (to thread the answer).

The bound is on the STORED representation (`flows.db.dumps`, ASCII-escaped JSON), in bytes, whatever
the operator sets `VEXA_FLOWS_MAIL_BODY_MAX` to and whatever the sender puts in the mail: a 200 KB
body, a 50 KB subject, quoted history, attachments.

Both intakes are covered: the mailbox poller (`flows_integrations.mailbox.handle`) and the
`mailbox` sensor kind (`flows_sensors.mailbox.MailboxSensor`).
"""
from __future__ import annotations

import json
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows_defs.production as production  # noqa: E402
from flows import Reaction, Registry, StepCtx  # noqa: E402
from flows.db import dumps  # noqa: E402
from flows_integrations import mailbox  # noqa: E402
from flows_integrations.inbox import from_rfc822  # noqa: E402
from flows_sensors.base import SensorRow  # noqa: E402
from flows_sensors.mailbox import MailboxSensor  # noqa: E402
from flows_steps.emailx import register_thread  # noqa: E402
from mail_auth_fixture import authed  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

#: The declared ceiling, in bytes of the stored JSON. Asserted as a literal so a change to the
#: constant is a change to this file too.
N = 36 * 1024
FIELDS = {"uid", "session", "from_addr", "text", "subject", "orig_msgid", "organizer"}

SELF = "vexa@acme.test"
ANNA = "anna@acme.test"
THREAD = "<t1@vexa.ai>"
OWN = "Yes, Tuesday at 10 works for the whole team. Please move the review."
HISTORY = "QUOTEDHISTORY"
ATTACHED = "ATTACHMENTCONTENT"
HTMLONLY = "HTMLONLYCONTENT"


def big_mail(*, own_words=200_000, subject_len=50_000, outlook=False) -> bytes:
    """A large real-shaped reply: the sender's words, a quoted thread, an HTML alternative and a
    text attachment, under a folded 50 KB subject."""
    own = OWN + "\n" + ("All good on our side, see notes. " * (own_words // 33))
    if outlook:
        history = "\n-----Original Message-----\nFrom: Vexa\nSent: Monday\n" + (HISTORY + " ") * 5000
    else:
        history = ("\nOn Mon, 20 Oct 2026 at 09:00, Vexa <vexa@acme.test> wrote:\n"
                   + "\n".join(f"> {HISTORY} line {i}" for i in range(5000)))
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(own + history, "plain", "utf-8"))
    alt.attach(MIMEText(f"<p>{HTMLONLY}</p>" * 1000, "html", "utf-8"))
    root = MIMEMultipart("mixed")
    root.attach(alt)
    att = MIMEText((ATTACHED + "\n") * 10_000, "plain", "utf-8")
    att.add_header("Content-Disposition", "attachment", filename="server.log")
    root.attach(att)
    root["From"] = f"Anna <{ANNA}>"
    root["To"] = SELF
    root["Subject"] = "Re: Minutes " + "S" * subject_len
    root["Message-ID"] = "<reply-1@acme.test>"
    root["In-Reply-To"] = THREAD
    return root.as_bytes()


def html_only_with_text_attachment() -> bytes:
    root = MIMEMultipart("mixed")
    root.attach(MIMEText(f"<p>{OWN}</p>", "html", "utf-8"))
    att = MIMEText((ATTACHED + "\n") * 50_000, "plain", "utf-8")
    att.add_header("Content-Disposition", "attachment", filename="dump.txt")
    root.attach(att)
    root["From"] = ANNA
    root["Subject"] = "Re: Minutes"
    root["Message-ID"] = "<reply-2@acme.test>"
    root["In-Reply-To"] = THREAD
    return root.as_bytes()


def parsed(raw: bytes):
    m = from_rfc822(raw, cursor="5", ext_id="5")
    # The fixture does not sign mail; `authed` adds the DMARC pass the intake requires of every sender.
    return type(m)(**{**m.__dict__, "headers": authed(m.frm, m.headers)})


def admit_via_poller(raw: bytes, reg=None) -> tuple[str, dict]:
    db = SqliteDB()
    if reg is None:
        from flows import Done, EventType
        reg = Registry()

        @reg.step
        def noop(ctx):
            return Done()
        reg.flow(name="email_chat", version=1, on=EventType("mail.reply"), steps=[noop])
    register_thread(db, THREAD, "7", "meet-97")
    out = mailbox.handle(db, reg, _Clock(), SELF, parsed(raw), {ANNA: "7"}.get,
                         lambda u: True, lambda e: "99")
    assert out == ("thread_reply", 1), out
    (stored,), = db.execute("SELECT subject_refs FROM reaction")
    return stored, json.loads(stored)


class _Clock:
    def now(self):
        return 1_900_000_000.0


def admit_via_sensor(raw: bytes) -> tuple[str, dict]:
    db = SqliteDB()
    register_thread(db, THREAD, "8", "main")

    class _Inbox:
        name = "fake"
        msgs = [parsed(raw)]

        def address(self):
            return SELF

        def fetch(self, cursor):
            return self.msgs

        def tail_cursor(self):
            return "5"

    sensor = MailboxSensor(inbox_factory=_Inbox, db_factory=lambda: db,
                           known_uid={ANNA: "8"}.get, rules=lambda: {},
                           clock=lambda: 1_900_000_000.0)
    tick = sensor.tick(SensorRow(scope="instance", name="info", kind="mailbox", status="active"), "4")
    (change,) = [c for c in tick.changes if c.event_type == "mail.reply"]
    return dumps(change.refs), change.refs


# ── the bound (red on the tree before it) ─────────────────────────────────────────────────────

def test_a_large_reply_stores_at_most_n_bytes_at_the_default_cap(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_MAIL_BODY_MAX", raising=False)
    stored, refs = admit_via_poller(big_mail())
    assert len(stored.encode()) <= N, f"{len(stored.encode())} bytes stored for one reply"
    assert set(refs) == FIELDS


def test_an_operator_cannot_raise_the_cap_past_the_bound(monkeypatch):
    """`VEXA_FLOWS_MAIL_BODY_MAX` tunes the excerpt inside the bound; it never lifts it."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "10000000")
    stored, refs = admit_via_poller(big_mail())
    assert len(stored.encode()) <= N, f"{len(stored.encode())} bytes stored for one reply"
    assert len(refs["text"]) <= mailbox.BODY_CEILING + len(mailbox.BODY_ELISION) + 20


def test_non_ascii_text_is_bounded_in_stored_bytes_not_characters(monkeypatch):
    """Stored JSON escapes every non-ASCII character to six bytes or more; a character cap alone
    lets the row grow six-fold."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "16000")
    alt = MIMEText("Да, вторник подходит. 🙂 " * 20_000, "plain", "utf-8")
    alt["From"], alt["Subject"] = ANNA, "Re: " + "Тема" * 20_000
    alt["Message-ID"], alt["In-Reply-To"] = "<reply-3@acme.test>", THREAD
    stored, _ = admit_via_poller(alt.as_bytes())
    assert len(stored.encode()) <= N, f"{len(stored.encode())} bytes stored for one reply"


def test_the_sensor_intake_is_bounded_the_same_way(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "10000000")
    stored, refs = admit_via_sensor(big_mail())
    assert len(stored.encode()) <= N, f"{len(stored.encode())} bytes in the sensor's change"
    assert set(refs) == FIELDS


def test_no_quoted_history_or_attachment_or_html_reaches_the_reaction(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "10000000")
    for raw in (big_mail(own_words=100), big_mail(own_words=100, outlook=True)):
        stored, refs = admit_via_poller(raw)
        assert refs["text"].startswith(OWN)
        for marker in (HISTORY, ATTACHED, HTMLONLY):
            assert marker not in stored, marker


def test_an_attachment_is_never_read_as_the_body():
    """An HTML-only reply carrying a text attachment: the attachment is a file, not what they wrote."""
    stored, refs = admit_via_poller(html_only_with_text_attachment())
    assert ATTACHED not in stored
    assert refs["text"] == ""


def test_the_declared_constant_is_the_tested_bound():
    assert mailbox.REPLY_REFS_MAX_BYTES == N


# ── positive controls: every consuming step still gets what it needs ─────────────────────────

def test_a_short_reply_is_stored_exactly(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_MAIL_BODY_MAX", raising=False)
    msg = MIMEText(OWN + "\n\n> what we sent\n", "plain", "utf-8")
    msg["From"], msg["Subject"] = f"Anna <{ANNA}>", "Re: Minutes for Monday"
    msg["Message-ID"], msg["In-Reply-To"] = "<reply-4@acme.test>", THREAD
    _, refs = admit_via_poller(msg.as_bytes())
    assert refs["text"].strip() == OWN
    assert {**refs, "text": OWN} == {"uid": "7", "session": "meet-97", "from_addr": ANNA, "text": OWN,
                    "subject": "Re: Minutes for Monday", "orig_msgid": "<reply-4@acme.test>",
                    "organizer": ANNA}


def test_intake_to_feedback_turn_and_email_reply_end_to_end(monkeypatch):
    """The real production registry: the big mail is admitted by the poller, and the two steps of
    `email_chat` run on the reaction exactly as stored — the prompt carries the sender's words and
    name, the answer goes to the sender, on the thread, under their subject."""
    monkeypatch.delenv("VEXA_FLOWS_MAIL_BODY_MAX", raising=False)
    db = SqliteDB()
    reg = Registry()
    production.build(reg, db)
    register_thread(db, THREAD, "7", "meet-97")
    out = mailbox.handle(db, reg, _Clock(), SELF, parsed(big_mail()), {ANNA: "7"}.get,
                         lambda u: True, lambda e: "99")
    assert out == ("thread_reply", 1)
    (rid, raw_refs, flow), = db.execute("SELECT reaction_id, subject_refs, flow FROM reaction")
    assert flow == "email_chat"
    refs = json.loads(raw_refs)
    r = Reaction(rid, "sid", "mail.reply", refs, flow, 1, "feedback_turn", "running", 1, 0.0,
                 None, None, None)

    seen, outbox = {}, iter([(None, "h0"), ("Tuesday it is.", "h1")])
    monkeypatch.setattr(production.ag, "collect_outbox", lambda uid, s, h: next(outbox))
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, s, p, room=None, **kw: seen.update(uid=uid, session=s, prompt=p) or 0)
    scratch = {}
    ctx = StepCtx(reaction=r, effect_key="k", prior={}, clock_now=1_900_000_000.0,
                  scratch=scratch, flow=None)
    reg.steps["feedback_turn"](ctx)
    assert (seen["uid"], seen["session"]) == ("7", "meet-97")
    assert OWN in seen["prompt"] and f"BEGIN UNTRUSTED EMAIL FROM {ANNA}" in seen["prompt"]
    done = reg.steps["feedback_turn"](ctx)
    assert done.result == {"reply": "Tuesday it is."}

    sent = {}
    def fake_mail(kind, to, subject, body, **kw):
        sent.update(kind=kind, to=to, subject=subject, body=body, **kw)
        return "<answer-1@vexa.ai>"
    monkeypatch.setattr(production, "mail", fake_mail)
    r2 = Reaction(rid, "sid", "mail.reply", refs, flow, 1, "email_reply", "running", 1, 0.0,
                  None, None, None)
    reg.steps["email_reply"](StepCtx(reaction=r2, effect_key="k2", prior={"feedback_turn": done.result},
                                     clock_now=1_900_000_000.0, scratch=scratch, flow=None))
    assert sent["to"] == ANNA and sent["body"] == "Tuesday it is."
    assert sent["in_reply_to"] == "<reply-1@acme.test>"
    assert sent["subject"].startswith("Re: Re: Minutes SSSS")
