"""The MAIL TRANSPORT SEAM — one interface, three wirings.

The product's front door is a mailbox. Which mailbox technology it is must not reach the engine,
the flows or the steps: `mailbox.py` polls a transport, `emailx.py` sends through one, and both
select it from a single env var.

    VEXA_MAIL_TRANSPORT = gmail   (default) — IMAP/SMTP with Google's hosts
                        = imap             — IMAP/SMTP with YOUR hosts (Exchange with IMAP on)
                        = graph            — Microsoft Graph, client-credentials (M365)

Contracts every transport keeps, identical across wirings — they are the ones the live witness
paid for, so a new transport that breaks any of them is a regression not a variant:

  C1  **durable cursor** — a restart resumes where it stopped and never re-admits history; on
      FIRST boot the cursor anchors at the CURRENT tail, so an existing mailbox is not replayed.
  C2  **threading** — every outbound conversation mail returns its real `Message-ID`, which the
      caller registers in `mail_thread`; inbound `In-Reply-To`/`References` are surfaced so the
      reply routes by THREAD, never by sender.
  C3  **ICS attachments** are read and decoded (the invite is an attachment, not a body).
  C4  **no sleeping, no internal polling** — `fetch()` returns what is there and stops.

Stdlib only (imaplib/smtplib/urllib) — matches `flows_steps/common.py`'s `http()` idiom.
"""
from __future__ import annotations

import email as email_mod
import email.utils
import os
import subprocess
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable, Iterator

from flows_integrations.ics import decode_ics

VAULT = os.path.expanduser("~/dev/vexa-secrets/business/vexa-mail.enc.env")

ICS_TYPES = ("text/calendar", "application/ics", "application/calendar")


def creds() -> tuple[str, str]:
    """(address, password) for the IMAP/SMTP wirings — env first, SOPS vault as the fallback."""
    if not (os.environ.get("VEXA_MAIL_ADDR") and os.environ.get("VEXA_MAIL_APP_PASSWORD")):
        out = subprocess.run(["sops", "-d", VAULT], check=True, capture_output=True, text=True).stdout
        for line in out.splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.environ["VEXA_MAIL_ADDR"], os.environ["VEXA_MAIL_APP_PASSWORD"]


@dataclass(frozen=True)
class InboundMessage:
    """One inbound mail, transport-independent. `cursor` is the transport-native position AFTER
    this message — the poller persists it once the message has been routed (C1)."""
    cursor: str
    message_id: str
    frm: str
    subject: str
    headers: dict = field(default_factory=dict)
    body: str = ""
    ics: str | None = None


class MailTransport:
    """The seam. Subclasses implement five methods; nothing else may vary."""

    name = "abstract"

    def address(self) -> str:
        raise NotImplementedError

    def tail_cursor(self) -> str:
        """The position of the CURRENT tail — first boot anchors here (C1: no history replay)."""
        raise NotImplementedError

    def fetch(self, cursor: str) -> Iterator[InboundMessage]:
        raise NotImplementedError

    def send(self, to: str, subject: str, body: str, *, in_reply_to: str | None = None) -> str:
        raise NotImplementedError

    def send_calendar_reply(self, to: str, subject: str, text: str, ics: str) -> str:
        raise NotImplementedError


# --------------------------------------------------------------------------------------------
# IMAP + SMTP — the original wiring, hosts no longer hardcoded.
#
# `gmail` and `imap` are THE SAME CODE PATH: gmail only supplies different host defaults. That is
# the whole point — an Exchange/M365 mailbox with IMAP enabled needs zero new code, just
# VEXA_MAIL_IMAP_HOST=outlook.office365.com VEXA_MAIL_SMTP_HOST=smtp.office365.com
# VEXA_MAIL_SMTP_PORT=587 VEXA_MAIL_SMTP_STARTTLS=1.
# --------------------------------------------------------------------------------------------
GMAIL_DEFAULTS = {"imap_host": "imap.gmail.com", "smtp_host": "smtp.gmail.com"}


class ImapSmtpTransport(MailTransport):
    name = "imap"

    def __init__(self, *, defaults: dict | None = None):
        d = defaults or {}
        self.imap_host = os.environ.get("VEXA_MAIL_IMAP_HOST") or d.get("imap_host", "")
        self.imap_port = int(os.environ.get("VEXA_MAIL_IMAP_PORT", "993"))
        self.folder = os.environ.get("VEXA_MAIL_IMAP_FOLDER", "INBOX")
        self.smtp_host = os.environ.get("VEXA_MAIL_SMTP_HOST") or d.get("smtp_host", "")
        self.smtp_port = int(os.environ.get("VEXA_MAIL_SMTP_PORT", "465"))
        self.starttls = os.environ.get("VEXA_MAIL_SMTP_STARTTLS", "0") == "1"
        if not self.imap_host or not self.smtp_host:
            raise ValueError("VEXA_MAIL_IMAP_HOST / VEXA_MAIL_SMTP_HOST are required for "
                             "VEXA_MAIL_TRANSPORT=imap (the gmail transport supplies defaults)")

    def address(self) -> str:
        return creds()[0]

    def _imap(self):
        import imaplib
        addr, pw = creds()
        im = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        im.login(addr, pw)
        im.select(self.folder)
        return im

    def tail_cursor(self) -> str:
        with self._imap() as im:
            _, d = im.uid("search", None, "ALL")
            uids = d[0].split() if d and d[0] else []
            return str(int(uids[-1])) if uids else "0"

    def fetch(self, cursor: str) -> Iterator[InboundMessage]:
        low = int(cursor or 0)
        with self._imap() as im:
            _, d = im.uid("search", None, f"UID {low + 1}:*")
            for raw in (d[0].split() if d and d[0] else []):
                uid = int(raw)
                if uid <= low:
                    continue                    # IMAP's `n:*` returns the last UID even when empty
                _, md = im.uid("fetch", raw, "(RFC822)")
                yield _from_rfc822(md[0][1], str(uid))

    def _smtp(self):
        import smtplib
        addr, pw = creds()
        if self.starttls:
            s = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20)
            s.starttls()
        else:
            s = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=20)
        s.login(addr, pw)
        return s

    def send(self, to: str, subject: str, body: str, *, in_reply_to: str | None = None) -> str:
        addr = creds()[0]
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = f"Vexa <{addr}>", to, subject
        m["Message-ID"] = email.utils.make_msgid(domain=addr.split("@")[1])
        if in_reply_to:
            m["In-Reply-To"] = m["References"] = in_reply_to
        m.set_content(body)
        with self._smtp() as s:
            s.send_message(m)
        return m["Message-ID"]

    def send_calendar_reply(self, to: str, subject: str, text: str, ics: str) -> str:
        addr = creds()[0]
        msg = MIMEMultipart("mixed")
        msg["From"], msg["To"], msg["Subject"] = f"Vexa <{addr}>", to, subject
        msg["Message-ID"] = email.utils.make_msgid(domain=addr.split("@")[1])
        msg.attach(MIMEText(text, "plain"))
        cal = MIMEText(ics, "calendar")
        cal.set_param("method", "REPLY")
        msg.attach(cal)
        with self._smtp() as s:
            s.send_message(msg)
        return msg["Message-ID"]


def _from_rfc822(raw: bytes, cursor: str) -> InboundMessage:
    """RFC822 bytes → InboundMessage. Shared by IMAP and by the Graph transport's MIME path."""
    msg = email_mod.message_from_bytes(raw)
    body, ics = "", None
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain" and not body:
            payload = part.get_payload(decode=True) or b""
            body = payload.decode(errors="replace")
        if ct in ICS_TYPES or (part.get_filename() or "").lower().endswith(".ics"):
            ics = decode_ics(part.get_payload(decode=True) or b"")
    return InboundMessage(cursor=cursor,
                          message_id=(msg.get("Message-ID", "") or "").strip(),
                          frm=email_mod.utils.parseaddr(msg.get("From", ""))[1].lower(),
                          subject=msg.get("Subject", "") or "",
                          headers=dict(msg.items()), body=body, ics=ics)


# --------------------------------------------------------------------------------------------
# Cursor persistence — one row, transport-agnostic.
#
# `mail_cursor.uid` (INTEGER) predates this seam and only ever held an IMAP UID. Graph's position
# is a delta token or an ISO timestamp, so the row grew a nullable `token TEXT`. Reads prefer
# `token`; an existing IMAP deployment whose row has only `uid` keeps working untouched.
# --------------------------------------------------------------------------------------------
def has_token_column(db) -> bool:
    """`schema.sql` is CREATE TABLE IF NOT EXISTS with no migration runner, so a mailbox that was
    already deployed before this seam has a `mail_cursor` WITHOUT `token`. Detect rather than
    assume — an IMAP deployment must keep running untouched, and Graph must fail loudly (it
    cannot store its position in an INTEGER) instead of silently re-reading the inbox.
    Fix on such a database is one line: ALTER TABLE mail_cursor ADD COLUMN token TEXT;"""
    try:
        db.execute("SELECT token FROM mail_cursor WHERE id = 1")
        return True
    except Exception:  # noqa: BLE001 — "column does not exist" is a schema fact, not a failure
        return False


def read_cursor(db) -> str | None:
    if has_token_column(db):
        row = db.execute("SELECT uid, token FROM mail_cursor WHERE id = 1")
        if not row:
            return None
        uid, token = row[0][0], row[0][1]
        return token if token else str(uid or 0)
    row = db.execute("SELECT uid FROM mail_cursor WHERE id = 1")
    return str(row[0][0] or 0) if row else None


def write_cursor(db, cursor: str) -> None:
    numeric = str(cursor).isdigit()
    uid = int(cursor) if numeric else 0
    if has_token_column(db):
        db.execute("INSERT INTO mail_cursor (id, uid, token) VALUES (1, :u, :t) "
                   "ON CONFLICT (id) DO UPDATE SET uid = :u, token = :t",
                   {"u": uid, "t": str(cursor)})
        return
    if not numeric:
        raise RuntimeError("mail_cursor has no `token` column but this transport's cursor is not "
                           "an integer — run: ALTER TABLE mail_cursor ADD COLUMN token TEXT;")
    db.execute("INSERT INTO mail_cursor (id, uid) VALUES (1, :u) "
               "ON CONFLICT (id) DO UPDATE SET uid = :u", {"u": uid})


# --------------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------------
_CACHED: dict[str, MailTransport] = {}
_BUILDERS: dict[str, Callable[[], MailTransport]] = {
    "gmail": lambda: ImapSmtpTransport(defaults=GMAIL_DEFAULTS),
    "imap": lambda: ImapSmtpTransport(),
    "graph": lambda: __import__("flows_integrations.graph_transport", fromlist=["x"]).GraphTransport(),
}


def transport_name() -> str:
    return (os.environ.get("VEXA_MAIL_TRANSPORT") or "gmail").strip().lower()


def get_transport(name: str | None = None, *, fresh: bool = False) -> MailTransport:
    key = (name or transport_name())
    if key not in _BUILDERS:
        raise ValueError(f"unknown VEXA_MAIL_TRANSPORT={key!r} (gmail | imap | graph)")
    if fresh or key not in _CACHED:
        _CACHED[key] = _BUILDERS[key]()
    return _CACHED[key]


def reset_transport_cache() -> None:
    _CACHED.clear()
