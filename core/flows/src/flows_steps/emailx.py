"""Email effects — REAL mail through the selected transport, thread-registered. Every outbound
conversation mail records its Message-ID → (subject, session) in mail_thread so the integration
routes replies by THREAD, never by sender (the wrong-mail-answered-onboarding lesson).

The wire (Gmail-IMAP/SMTP · generic IMAP/SMTP · Microsoft Graph) is chosen by
`VEXA_MAIL_TRANSPORT` and lives in `flows_integrations/mail_transport.py`. Step code below is
transport-blind: it asks for a send and gets back the Message-ID it must register."""
from __future__ import annotations

import time

from flows_integrations.mail_transport import creds, get_transport

__all__ = ["creds", "send", "register_thread", "send_rsvp_accept", "build_rsvp_ics"]


def send(to: str, subject: str, body: str, *, in_reply_to: str | None = None) -> str:
    return get_transport().send(to, subject, body, in_reply_to=in_reply_to)


def register_thread(db, message_id: str, subject_uid: str, session: str) -> None:
    db.execute("""INSERT INTO mail_thread (message_id, subject_uid, session, created_at)
                  VALUES (:m,:u,:s,:t) ON CONFLICT (message_id) DO NOTHING""",
               {"m": message_id, "u": subject_uid, "s": session, "t": time.time()})


def build_rsvp_ics(self_addr: str, organizer_email: str, *, ics_uid: str,
                   start_epoch: float, title: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dtstart = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(start_epoch))
    return "\r\n".join([
        "BEGIN:VCALENDAR", "PRODID:-//Vexa//flows//EN", "VERSION:2.0", "METHOD:REPLY",
        "BEGIN:VEVENT", f"UID:{ics_uid}", "SEQUENCE:0", f"DTSTAMP:{stamp}", f"DTSTART:{dtstart}",
        f"ORGANIZER:mailto:{organizer_email}",
        f"ATTENDEE;PARTSTAT=ACCEPTED;CN=Vexa:mailto:{self_addr}",
        f"SUMMARY:{title}", "END:VEVENT", "END:VCALENDAR", ""])


def send_rsvp_accept(organizer_email: str, *, ics_uid: str, start_epoch: float, title: str) -> str:
    """iMIP REPLY from minimal fields — Google flips this mailbox to 'Yes' in the guest list."""
    tp = get_transport()
    ics = build_rsvp_ics(tp.address(), organizer_email, ics_uid=ics_uid,
                         start_epoch=start_epoch, title=title)
    return tp.send_calendar_reply(organizer_email, f"Accepted: {title}",
                                  "Vexa will attend and take the minutes.", ics)
