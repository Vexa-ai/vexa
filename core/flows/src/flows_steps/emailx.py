"""Email effects — REAL SMTP, thread-registered. Every outbound conversation mail records its
Message-ID → (subject, session) in mail_thread so the integration routes replies by THREAD,
never by sender (the wrong-mail-answered-onboarding lesson)."""
from __future__ import annotations

import email.utils
import os
import smtplib
import subprocess
import time
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

VAULT = os.path.expanduser("~/dev/vexa-secrets/business/vexa-mail.enc.env")


def creds() -> tuple[str, str]:
    if not (os.environ.get("VEXA_MAIL_ADDR") and os.environ.get("VEXA_MAIL_APP_PASSWORD")):
        out = subprocess.run(["sops", "-d", VAULT], check=True, capture_output=True, text=True).stdout
        for line in out.splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.environ["VEXA_MAIL_ADDR"], os.environ["VEXA_MAIL_APP_PASSWORD"]


def send(to: str, subject: str, body: str, *, in_reply_to: str | None = None) -> str:
    addr, pw = creds()
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"Vexa <{addr}>", to, subject
    m["Message-ID"] = email.utils.make_msgid(domain=addr.split("@")[1])
    if in_reply_to:
        m["In-Reply-To"] = m["References"] = in_reply_to
    m.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
        s.login(addr, pw)
        s.send_message(m)
    return m["Message-ID"]


def register_thread(db, message_id: str, subject_uid: str, session: str) -> None:
    db.execute("""INSERT INTO mail_thread (message_id, subject_uid, session, created_at)
                  VALUES (:m,:u,:s,:t) ON CONFLICT (message_id) DO NOTHING""",
               {"m": message_id, "u": subject_uid, "s": session, "t": time.time()})


def send_rsvp_accept(organizer_email: str, *, ics_uid: str, start_epoch: float, title: str) -> str:
    """iMIP REPLY from minimal fields — Google flips this mailbox to 'Yes' in the guest list."""
    addr, pw = creds()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dtstart = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(start_epoch))
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "PRODID:-//Vexa//flows//EN", "VERSION:2.0", "METHOD:REPLY",
        "BEGIN:VEVENT", f"UID:{ics_uid}", "SEQUENCE:0", f"DTSTAMP:{stamp}", f"DTSTART:{dtstart}",
        f"ORGANIZER:mailto:{organizer_email}",
        f"ATTENDEE;PARTSTAT=ACCEPTED;CN=Vexa:mailto:{addr}",
        f"SUMMARY:{title}", "END:VEVENT", "END:VCALENDAR", ""])
    msg = MIMEMultipart("mixed")
    msg["From"], msg["To"], msg["Subject"] = f"Vexa <{addr}>", organizer_email, f"Accepted: {title}"
    msg["Message-ID"] = email.utils.make_msgid(domain=addr.split("@")[1])
    msg.attach(MIMEText("Vexa will attend and take the minutes.", "plain"))
    cal = MIMEText(ics, "calendar")
    cal.set_param("method", "REPLY")
    msg.attach(cal)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
        s.login(addr, pw)
        s.send_message(msg)
    return msg["Message-ID"]
