"""mailbox integration — its own process: the REAL inbox becomes facts.

  ICS attachment (with a Meet link)      → invite.received   (dedup: the ICS UID)
  reply on a registered thread           → mail.reply        (dedup: inbound Message-ID)
  anything else                          → logged, ignored

Threading is the law here: a reply routes by In-Reply-To/References looked up in mail_thread —
never by sender. Cursor is durable (mail_cursor row) — restarts resume, never re-admit.

WHICH mailbox technology this is stops at `mail_transport.get_transport()`: Gmail-IMAP,
generic IMAP (Exchange with IMAP enabled) and Microsoft Graph all arrive here as the same
`InboundMessage`. Routing, admission and dedup below are transport-blind, by design."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows import Registry, SystemClock, admit, postgres_db
from flows_defs import production
from flows_integrations.ics import parse_ics  # re-exported: the parser moved, the import did not
from flows_integrations.mail_transport import (
    get_transport,
    read_cursor,
    transport_name,
    write_cursor,
)
from flows_steps.common import db_url

__all__ = ["parse_ics", "route", "strip_quotes", "handle", "main"]

POLL_SECONDS = 12


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
        ev = parse_ics(ics)
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
    """One inbound message → at most one admission. Transport-blind and service-blind: every
    outside reach is injected, so the whole intake is drivable offline."""
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
              source_event_id=f"mail-{msg.message_id or msg.cursor}",
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
    tp = get_transport()

    cursor = read_cursor(db)
    if cursor is None:
        # first boot: anchor at the CURRENT inbox tail — history is never replayed
        cursor = sys.argv[1] if len(sys.argv) > 1 else tp.tail_cursor()
        write_cursor(db, cursor)
    print(f"mailbox integration up · transport {transport_name()} · cursor {cursor!r}", flush=True)

    from flows_steps.common import ADMIN_API, ADMIN_KEY, ensure_platform_user
    from flows_steps.common import http as _http
    from flows_steps.common import scaffolded as _scaff

    def _known_uid(e: str):
        code, u = _http("GET", f"{ADMIN_API}/admin/users/email/{e}", {"X-Admin-API-Key": ADMIN_KEY})
        return u.get("id") if code == 200 else None

    self_addr = tp.address().lower()
    while True:
        try:
            for msg in tp.fetch(cursor):
                out = handle(db, reg, clock, self_addr, msg, _known_uid, _scaff,
                             ensure_platform_user)
                if out is None:
                    print(f"ignored mail from {msg.frm} ({msg.subject[:40]!r})", flush=True)
                else:
                    kind, n = out
                    print(f"{kind} {msg.frm} → admitted {n}", flush=True)
                cursor = msg.cursor
                write_cursor(db, cursor)
        except Exception as e:  # noqa: BLE001
            print(f"poll hiccup: {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
