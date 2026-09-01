"""mailbox integration — its own process: the REAL inbox becomes facts.

  ICS attachment (with a meeting link)   → invite.received   (dedup: the ICS UID)
  reply on a registered thread           → mail.reply        (dedup: inbound Message-ID)
  anything else                          → logged, ignored

Threading is the law here: a reply routes by In-Reply-To/References looked up in mail_thread —
never by sender. Cursor is durable (mail_cursor row) — restarts resume, never re-admit.

WHICH inbox this is stops at `inbox.get_inbox()` — routing, admission and dedup below are
source-blind by design:

    VEXA_MAIL_INBOX = imap     (default) — IMAP against imap.gmail.com, behaviour unchanged
                    = mailpit           — the dev stack's mail double (REST only, no IMAP)
    VEXA_MAILPIT_URL           — mailpit's HTTP base (default http://127.0.0.1:8025)
    VEXA_MAIL_ADDR             — the address this inbox answers as; mailpit filters on it
    VEXA_MAILPIT_LOOKBACK_S    — re-scan window behind the watermark (default 300)

Both sources fetch the raw RFC822 source and go through the same parse, so a Gmail invite and a
mailpit invite produce byte-identical facts. See `flows_integrations/inbox.py` for the contracts.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows import Registry, SystemClock, admit, postgres_db
from flows_defs import production
from flows_integrations.inbox import get_inbox
from flows_steps.common import db_url

POLL_SECONDS = 12

# A meeting link is Meet or Zoom. Meet stays byte-for-byte the pattern it always was (case
# sensitive, lowercase code); Zoom is the DNA corpus's platform — those invites are zoom.us.
MEET_URL = re.compile(r"https://meet\.google\.com/[a-z-]+")
ZOOM_URL = re.compile(r"https://(?:[A-Za-z0-9-]+\.)*zoom\.us/j/\d+(?:\?pwd=[A-Za-z0-9._%~-]+)?")


def _meeting_url(text: str) -> str | None:
    m = MEET_URL.search(text) or ZOOM_URL.search(text)
    return m.group(0) if m else None


def _unfold(ics: str) -> str:
    """RFC 5545 line folding: a CRLF followed by one space or tab continues the line. Real
    invites fold — a Zoom URL with a `?pwd=` is long enough to be split mid-token, and an
    ATTENDEE line with a CN and a mailto is longer still."""
    return re.sub(r"\r?\n[ \t]", "", ics)


def parse_ics(ics: str, self_addr: str | None = None) -> dict | None:
    ics = _unfold(ics)
    if "BEGIN:VEVENT" not in ics:
        return None                       # no event block — never fall back to scanning VTIMEZONE
    ve = ics.split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0]
    url = _meeting_url(ve) or _meeting_url(ics)
    if not url:
        return None
    org = re.search(r"ORGANIZER[^:]*:(?:mailto:)?([^\s]+)", ve, re.I)
    dt = re.search(r"DTSTART(?:;TZID=([^:;]+))?[^:]*:(\d{8}T\d{6})(Z?)", ve)
    uid = re.search(r"^UID:(.+)$", ve, re.M)
    summ = re.search(r"^SUMMARY:(.+)$", ve, re.M)
    desc = re.search(r"^DESCRIPTION:(.*)$", ve, re.M)
    group = None
    gm = re.search(r"#group:([\w-]+)", ics)
    if gm:
        group = gm.group(1)
    # Who else is in the room. The organizer stays exactly what it was — this is an ADDITIONAL
    # ref, and every existing consumer of refs is untouched. Our own address is never a
    # participant: we are the notetaker, and an onboarding aimed at ourselves is an echo loop.
    me = (self_addr if self_addr is not None else os.environ.get("VEXA_MAIL_ADDR", "")).strip().lower()
    participants: list[str] = []
    for a in re.finditer(r"ATTENDEE[^\n]*?mailto:([^\s;,>\"]+)", ve, re.I):
        who = a.group(1).strip().lower()
        if who and who != me and who not in participants:
            participants.append(who)
    start = time.time() + 150
    if dt:
        import calendar as cal
        from datetime import datetime
        from zoneinfo import ZoneInfo
        t = time.strptime(dt.group(2), "%Y%m%dT%H%M%S")
        if dt.group(3) == "Z":
            start = cal.timegm(t)
        elif dt.group(1):
            start = datetime(*t[:6], tzinfo=ZoneInfo(dt.group(1))).timestamp()
        else:
            start = time.mktime(t)
    if start < time.time() - 86400:
        return None                       # a start >24h in the past is a parse artifact (the 1970
                                          # class) or a stale event — never admit it (a bot would
                                          # dispatch IMMEDIATELY on an ancient start)
    return {"organizer": (org.group(1).strip().lower() if org else ""),
            "url": url, "start": start,
            "ics_uid": (uid.group(1).strip() if uid else f"noid-{int(start)}"),
            "title": (summ.group(1).strip() if summ else "Meeting"),
            "group": group,
            "participants": participants}


def route(db, self_addr: str, frm: str, headers: dict, ics: str | None,
          known_uid, is_scaffolded) -> tuple[str, dict] | None:
    """The routing DECISION, pure: returns (kind, payload) or None (ignore).
      kind ∈ invite | thread_reply | known_user_mail | new_sender_mail
    `known_uid(email) -> uid|None` and `is_scaffolded(uid) -> bool` are injected so the
    routing storm can drive every branch with no services."""
    if not frm or frm == self_addr:
        return None
    if ics and "BEGIN:VEVENT" in ics:
        if "METHOD:REPLY" in ics or "METHOD:CANCEL" in ics:
            return None               # calendar machinery (our own RSVP echoes!) — never a
                                      # conversation, never a provisioning trigger (storm catch:
                                      # we nearly onboarded calendar-notification@google.com)
        ev = parse_ics(ics, self_addr)
        if ev and ev["organizer"]:
            return ("invite", ev)
        return None
    auto = (headers.get("Auto-Submitted", "no").lower() != "no"
            or headers.get("Precedence", "").lower() in ("bulk", "list", "junk")
            or any(t in frm for t in ("no-reply", "noreply", "mailer-daemon", "postmaster", "bounce")))
    ref = (headers.get("In-Reply-To") or "").strip() or           (headers.get("References", "").split() or [""])[-1]
    hit = db.execute("SELECT subject_uid, session FROM mail_thread WHERE message_id = :m",
                     {"m": ref}) if ref else []
    if hit:
        suid, session = hit[0]
        return ("thread_reply", {"uid": suid, "session": session})
    if auto:
        return None
    uid = known_uid(frm)
    if uid is not None:
        return ("known_user_mail", {"uid": str(uid),
                                    "session": "main" if is_scaffolded(str(uid)) else "onboarding"})
    return ("new_sender_mail", {"session": "onboarding"})


def strip_quotes(body: str) -> str:
    return "\n".join(l for l in body.strip().splitlines() if not l.strip().startswith(">"))[:2000]


def handle(db, reg, clock, self_addr: str, msg, known_uid, is_scaffolded,
           provision) -> tuple[str, int] | None:
    """One inbound message → at most one admission. Source-blind and service-blind: every reach
    outside is injected, so the whole intake is drivable offline."""
    decision = route(db, self_addr, msg.frm, msg.headers, msg.ics, known_uid, is_scaffolded)
    if decision is None:
        return None
    kind, payload = decision
    if kind == "invite":
        n = admit(db, reg, clock, source_event_id=f"ics-{payload['ics_uid']}",
                  event_type="invite.received", subject_refs=payload)
        return ("invite", n)
    if kind == "new_sender_mail":
        payload["uid"] = provision(msg.frm)
    n = admit(db, reg, clock,
              source_event_id=f"mail-{msg.message_id or msg.ext_id}",
              event_type="mail.reply",
              subject_refs={"uid": str(payload["uid"]), "session": payload["session"],
                            "from_addr": msg.frm, "text": strip_quotes(msg.body),
                            "subject": msg.subject, "orig_msgid": msg.message_id,
                            "organizer": msg.frm})
    return (kind, n)


def main() -> int:
    db = postgres_db(db_url())
    clock = SystemClock()
    reg = Registry()
    production.build(reg, db)     # the matcher needs the flow triggers; steps unused here
    inbox = get_inbox()

    cursor = inbox.restore(db)
    if cursor is None:
        # first boot: anchor at the CURRENT inbox tail — history is never replayed
        cursor = sys.argv[1] if len(sys.argv) > 1 else inbox.tail_cursor()
        inbox.anchor(db, cursor)
    print(f"mailbox integration up · inbox {inbox.name} · cursor {cursor!r}", flush=True)

    from flows_steps.common import ADMIN_API, ADMIN_KEY, ensure_platform_user
    from flows_steps.common import http as _http
    from flows_steps.common import scaffolded as _scaff

    def _known_uid(e: str):
        code, u = _http("GET", f"{ADMIN_API}/admin/users/email/{e}", {"X-Admin-API-Key": ADMIN_KEY})
        return u.get("id") if code == 200 else None

    while True:
        try:
            self_addr = inbox.address().lower()
            for msg in inbox.fetch(cursor):
                out = handle(db, reg, clock, self_addr, msg, _known_uid, _scaff,
                             ensure_platform_user)
                if out is None:
                    print(f"ignored mail from {msg.frm} ({msg.subject[:40]!r})", flush=True)
                else:
                    kind, n = out
                    print(f"{kind} {msg.frm} {msg.subject[:40]!r} → admitted {n}", flush=True)
                cursor = msg.cursor
                inbox.commit(db, msg)
        except Exception as e:  # noqa: BLE001
            print(f"poll hiccup: {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
