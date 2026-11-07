"""REAL mail for the product flow — Gmail via app password, credentials from the SOPS vault
(`~/dev/vexa-secrets/business/vexa-mail.enc.env`, keys VEXA_MAIL_ADDR / VEXA_MAIL_APP_PASSWORD).
Values are decrypted into process env only — never printed, never written to disk.

  send(to, subject, body[, in_reply_to])      — real SMTP (smtp.gmail.com:465)
  poll(since_uid) -> list[InboundMail]        — real IMAP (imap.gmail.com), new mail since cursor
  InboundMail: uid · from_addr · subject · body · in_reply_to · ics (ICS attachment text or None)

The poller is the INTEGRATION of the architecture: it turns mailbox state into facts —
an ICS attachment → invite.received; a reply → a resume signal for the blocked reaction."""
from __future__ import annotations

import email
import email.utils
import imaplib
import os
import smtplib
import subprocess
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

VAULT = os.path.expanduser("~/dev/vexa-secrets/business/vexa-mail.enc.env")


def _creds() -> tuple[str, str]:
    if not (os.environ.get("VEXA_MAIL_ADDR") and os.environ.get("VEXA_MAIL_APP_PASSWORD")):
        out = subprocess.run(["sops", "-d", VAULT], check=True, capture_output=True, text=True).stdout
        for line in out.splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.environ["VEXA_MAIL_ADDR"], os.environ["VEXA_MAIL_APP_PASSWORD"]


def send(to: str, subject: str, body: str, in_reply_to: Optional[str] = None) -> str:
    addr, pw = _creds()
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


@dataclass
class InboundMail:
    uid: int
    from_addr: str
    subject: str
    body: str
    in_reply_to: Optional[str]
    ics: Optional[str]


def poll(since_uid: int = 0) -> list[InboundMail]:
    addr, pw = _creds()
    out: list[InboundMail] = []
    with imaplib.IMAP4_SSL("imap.gmail.com") as im:
        im.login(addr, pw)
        im.select("INBOX")
        _, data = im.uid("search", None, f"UID {since_uid + 1}:*")
        for raw_uid in (data[0].split() if data and data[0] else []):
            uid = int(raw_uid)
            if uid <= since_uid:
                continue
            _, msg_data = im.uid("fetch", raw_uid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            body, ics = "", None
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain" and not body:
                    body = part.get_payload(decode=True).decode(errors="replace")
                if ct in ("text/calendar", "application/ics") or \
                        (part.get_filename() or "").endswith(".ics"):
                    ics = part.get_payload(decode=True).decode(errors="replace")
            out.append(InboundMail(
                uid=uid,
                from_addr=email.utils.parseaddr(msg.get("From", ""))[1],
                subject=msg.get("Subject", ""),
                body=body,
                in_reply_to=msg.get("In-Reply-To"),
                ics=ics))
    return out


def send_rsvp_accept(original_ics: str, organizer_email: str) -> str:
    """iMIP REPLY — accept the invitation over plain SMTP. Google Calendar processes the
    text/calendar METHOD:REPLY part and marks this mailbox 'Yes' in the organizer's guest list."""
    import re, time as _t
    addr, pw = _creds()
    ve = original_ics.split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0]
    def grab(field):
        r = re.search(rf"^({field}[^:\n]*:[^\n]+)$", ve, re.M)
        return r.group(1).strip() if r else None
    uid = grab("UID"); dtstart = grab("DTSTART"); dtend = grab("DTEND")
    seq = grab("SEQUENCE") or "SEQUENCE:0"
    summary = (grab("SUMMARY") or "SUMMARY:Meeting").split(":", 1)[1]
    org = grab("ORGANIZER") or f"ORGANIZER:mailto:{organizer_email}"
    stamp = _t.strftime("%Y%m%dT%H%M%SZ", _t.gmtime())
    reply_ics = "\r\n".join([
        "BEGIN:VCALENDAR", "PRODID:-//Vexa//flows//EN", "VERSION:2.0", "METHOD:REPLY",
        "BEGIN:VEVENT", uid, seq, f"DTSTAMP:{stamp}", dtstart or "", dtend or "", org,
        f"ATTENDEE;PARTSTAT=ACCEPTED;CN=Vexa:mailto:{addr}",
        f"SUMMARY:{summary}", "END:VEVENT", "END:VCALENDAR", ""])
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    import email.utils as eu, smtplib as sl
    msg = MIMEMultipart("mixed")
    msg["From"], msg["To"] = f"Vexa <{addr}>", organizer_email
    msg["Subject"] = f"Accepted: {summary}"
    msg["Message-ID"] = eu.make_msgid(domain=addr.split("@")[1])
    msg.attach(MIMEText(f"Vexa will attend and take the minutes.", "plain"))
    cal = MIMEText(reply_ics, "calendar")
    cal.set_param("method", "REPLY")
    msg.attach(cal)
    with sl.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as srv:
        srv.login(addr, pw)
        srv.send_message(msg)
    return msg["Message-ID"]
