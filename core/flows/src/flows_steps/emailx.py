"""Email effects — REAL SMTP, thread-registered. Every outbound conversation mail records its
Message-ID → (subject, session) in mail_thread so the integration routes replies by THREAD,
never by sender (the wrong-mail-answered-onboarding lesson)."""
from __future__ import annotations

import email.utils
import smtplib
import time
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import flows_config


def creds(*, login: bool = True) -> tuple[str, str]:
    """Address and password, always as a PAIR, and always from THE CONFIG CONTRACT.

    They used to come from different places: the guard fired when either was missing, a private
    credential store was decrypted, and os.environ.setdefault then refused to replace an address
    that was already set. A rig exporting VEXA_MAIL_ADDR and no password therefore logged into
    Gmail as vexa@storm.test with the production account's password — Gmail answered 535 and the
    error read as an expired credential for hours. A half-configured pair is not a configuration.

    AND THE PRIVATE STORE IS GONE (PRD decision 18c). This function used to shell out to a
    decrypt command against a path inside one developer's home directory whenever the pair was
    incomplete: product source reading a private file on one machine. It made the mail path
    unrunnable for anybody else, invisible to the config contract, and silently dependent on a
    binary nothing installs. Configuration is delivered by the deployment's own environment
    (P14) — a deployment that has not named these keys is REFUSED, by name, rather than guessed
    at. The refusal names the KEY and never the value, exactly as `require_admin_key` does.

    `login=False` is the mail DOUBLE: mailpit takes no credential at all, so the dogfood lane
    names a host and a port and no password, and demanding one there would break the only lane
    that can be rehearsed without reaching a real mailbox.
    """
    addr = flows_config.get("VEXA_MAIL_ADDR")
    pw = flows_config.get("VEXA_MAIL_APP_PASSWORD")
    missing = [k for k, value, needed in (("VEXA_MAIL_ADDR", addr, True),
                                          ("VEXA_MAIL_APP_PASSWORD", pw, login))
               if needed and not value]
    if missing:
        raise flows_config.ConfigError(
            "this flows deployment cannot name " + ", ".join(missing)
            + " — the mailbox credentials are delivered by the deployment's own environment (P14) "
              "and there is nothing behind them to fall back on: set each, or name "
              "VEXA_MAIL_SMTP_HOST to use a transport that takes no login.")
    return addr, pw


def _needs_login() -> bool:
    """Does this transport authenticate? Gmail does; the double does not. Asked separately from
    `_smtp` because the answer is needed BEFORE a socket is opened — the credentials are resolved
    while the message is still being built, so a deployment that cannot name them is refused
    without first connecting to a mail server."""
    return not flows_config.get("VEXA_MAIL_SMTP_HOST")


def _smtp():
    """Where mail actually goes.

    Hardcoding smtp.gmail.com meant the mail double this rig runs was never reachable: the invite
    path could not be rehearsed, only fired at real recipients. When VEXA_MAIL_SMTP_HOST is set we
    honour it; with nothing set, behaviour is exactly as before.
    """
    if _needs_login():
        return smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20), True
    return smtplib.SMTP(flows_config.get("VEXA_MAIL_SMTP_HOST"),
                        flows_config.get_int("VEXA_MAIL_SMTP_PORT"), timeout=20), False


def send(to: str, subject: str, body: str, *, in_reply_to: str | None = None) -> str:
    addr, pw = creds(login=_needs_login())
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"Vexa <{addr}>", to, subject
    m["Message-ID"] = email.utils.make_msgid(domain=addr.split("@")[1])
    if in_reply_to:
        m["In-Reply-To"] = m["References"] = in_reply_to
    m.set_content(body)
    conn, needs_login = _smtp()
    with conn as s:
        if needs_login:
            s.login(addr, pw)
        s.send_message(m)
    return m["Message-ID"]


def register_thread(db, message_id: str, subject_uid: str, session: str) -> None:
    db.execute("""INSERT INTO mail_thread (message_id, subject_uid, session, created_at)
                  VALUES (:m,:u,:s,:t) ON CONFLICT (message_id) DO NOTHING""",
               {"m": message_id, "u": subject_uid, "s": session, "t": time.time()})


def ics_escape(value) -> str:
    """One ICS property VALUE, escaped per RFC 5545 §3.3.11 — and de-folded first.

    The iMIP reply built its whole calendar body, and its `Subject` header, by raw interpolation
    of text that came off the invite we were answering: `UID`, `SUMMARY`, the organizer address
    (R-B15). A `SUMMARY` containing a CRLF does not corrupt the file — it CLOSES the property and
    opens whichever one the attacker names next, inside a REPLY we send as ourselves, signed by
    our own mailbox, into the organizer's calendar. `ATTENDEE;PARTSTAT=ACCEPTED;CN=…:mailto:…` is
    two lines of somebody else's text away.

    Order matters: backslash first, or every escape this function adds is escaped again. Newlines
    of every flavour become the literal `\n` the format defines, so nothing survives as a line
    break; a bare CR would otherwise fold under `_unfold`'s own rule on the receiving side.
    """
    out = str(value if value is not None else "")
    out = out.replace("\\", "\\\\")
    for ch in (";", ","):
        out = out.replace(ch, "\\" + ch)
    out = out.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
    return out


def header_safe(value) -> str:
    """One header VALUE with no line structure left in it. Python's `email` package raises on an
    embedded newline for `set()`, but MIMEMultipart's `__setitem__` does not always reach that
    check, and a `Subject` is not worth finding out — `title` comes off the same invite."""
    return " ".join(str(value if value is not None else "").split())


def send_rsvp_accept(organizer_email: str, *, ics_uid: str, start_epoch: float, title: str) -> str:
    """iMIP REPLY from minimal fields — Google flips this mailbox to 'Yes' in the guest list.

    Every value that came from the invite goes through `ics_escape` (and the Subject through
    `header_safe`): the title, the UID and the organizer address are all attacker-adjacent, and
    this is a message we sign with our own mailbox (R-B15)."""
    addr, pw = creds(login=_needs_login())
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dtstart = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(start_epoch))
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "PRODID:-//Vexa//flows//EN", "VERSION:2.0", "METHOD:REPLY",
        "BEGIN:VEVENT", f"UID:{ics_escape(ics_uid)}", "SEQUENCE:0", f"DTSTAMP:{stamp}",
        f"DTSTART:{dtstart}",
        f"ORGANIZER:mailto:{ics_escape(organizer_email)}",
        f"ATTENDEE;PARTSTAT=ACCEPTED;CN=Vexa:mailto:{ics_escape(addr)}",
        f"SUMMARY:{ics_escape(title)}", "END:VEVENT", "END:VCALENDAR", ""])
    msg = MIMEMultipart("mixed")
    msg["From"], msg["To"] = f"Vexa <{addr}>", organizer_email
    msg["Subject"] = header_safe(f"Accepted: {title}")
    msg["Message-ID"] = email.utils.make_msgid(domain=addr.split("@")[1])
    msg.attach(MIMEText("Vexa will attend and take the minutes.", "plain"))
    cal = MIMEText(ics, "calendar")
    cal.set_param("method", "REPLY")
    msg.attach(cal)
    conn, needs_login = _smtp()
    with conn as s:
        if needs_login:
            s.login(addr, pw)
        s.send_message(msg)
    return msg["Message-ID"]
