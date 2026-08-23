"""mailbox integration — its own process: the REAL inbox becomes facts.

  ICS attachment (with a Meet link)      → invite.received   (dedup: the ICS UID)
  reply on a registered thread           → mail.reply        (dedup: inbound Message-ID)
  anything else                          → logged, ignored

Threading is the law here: a reply routes by In-Reply-To/References looked up in mail_thread —
never by sender. Cursor is durable (mail_cursor row) — restarts resume, never re-admit."""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows import Registry, SystemClock, admit, postgres_db
from flows_defs import production
from flows_steps import emailx as mx
from flows_steps.common import db_url


def parse_ics(ics: str) -> dict | None:
    ve = ics.split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0]
    url = re.search(r"https://meet\.google\.com/[a-z-]+", ve) or \
        re.search(r"https://meet\.google\.com/[a-z-]+", ics)
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
    return {"organizer": (org.group(1).strip().lower() if org else ""),
            "url": url.group(0), "start": start,
            "ics_uid": (uid.group(1).strip() if uid else f"noid-{int(start)}"),
            "title": (summ.group(1).strip() if summ else "Meeting"),
            "group": group}


def strip_quotes(body: str) -> str:
    return "\n".join(l for l in body.strip().splitlines() if not l.strip().startswith(">"))[:2000]


def main() -> int:
    db = postgres_db(db_url())
    clock = SystemClock()
    reg = Registry()
    production.build(reg, db)     # the matcher needs the flow triggers; steps unused here

    row = db.execute("SELECT uid FROM mail_cursor WHERE id = 1")
    if row:
        cursor = row[0][0]
    else:
        # first boot: anchor at the CURRENT inbox tail — history is never replayed
        tail = 0
        import imaplib
        addr, pw = mx.creds()
        with imaplib.IMAP4_SSL("imap.gmail.com") as im:
            im.login(addr, pw); im.select("INBOX")
            _, d = im.uid("search", None, "ALL")
            uids = d[0].split() if d and d[0] else []
            tail = int(uids[-1]) if uids else 0
        cursor = int(sys.argv[1]) if len(sys.argv) > 1 else tail
        db.execute("INSERT INTO mail_cursor (id, uid) VALUES (1, :u) ON CONFLICT (id) DO NOTHING",
                   {"u": cursor})
    print(f"mailbox integration up · cursor uid {cursor}", flush=True)

    import email as email_mod
    import imaplib
    while True:
        try:
            addr, pw = mx.creds()
            with imaplib.IMAP4_SSL("imap.gmail.com") as im:
                im.login(addr, pw); im.select("INBOX")
                _, d = im.uid("search", None, f"UID {cursor + 1}:*")
                for raw in (d[0].split() if d and d[0] else []):
                    uid = int(raw)
                    if uid <= cursor:
                        continue
                    _, md = im.uid("fetch", raw, "(RFC822)")
                    msg = email_mod.message_from_bytes(md[0][1])
                    frm = email_mod.utils.parseaddr(msg.get("From", ""))[1].lower()
                    body, ics = "", None
                    for part in msg.walk():
                        ct = part.get_content_type()
                        if ct == "text/plain" and not body:
                            body = part.get_payload(decode=True).decode(errors="replace")
                        if ct in ("text/calendar", "application/ics") or \
                                (part.get_filename() or "").endswith(".ics"):
                            ics = part.get_payload(decode=True).decode(errors="replace")
                    if ics and "BEGIN:VEVENT" in ics and "METHOD:REPLY" not in ics:
                        ev = parse_ics(ics)
                        if ev and ev["organizer"] and frm != addr:
                            n = admit(db, reg, clock, source_event_id=f"ics-{ev['ics_uid']}",
                                      event_type="invite.received", subject_refs=ev)
                            print(f"ICS '{ev['title']}' {ev['organizer']} group={ev['group']} → admitted {n}", flush=True)
                    else:
                        ref = (msg.get("In-Reply-To") or "").strip() or \
                              (msg.get("References", "").split() or [""])[-1]
                        hit = db.execute("SELECT subject_uid, session FROM mail_thread "
                                         "WHERE message_id = :m", {"m": ref}) if ref else []
                        if hit and frm != addr:
                            suid, session = hit[0]
                            n = admit(db, reg, clock,
                                      source_event_id=f"mail-{msg.get('Message-ID','').strip() or uid}",
                                      event_type="mail.reply",
                                      subject_refs={"uid": suid, "session": session,
                                                    "from_addr": frm, "text": strip_quotes(body),
                                                    "subject": msg.get("Subject", ""),
                                                    "orig_msgid": msg.get("Message-ID", "").strip(),
                                                    "organizer": frm})
                            print(f"threaded reply {frm} → {session} → admitted {n}", flush=True)
                        elif frm != addr:
                            print(f"unthreaded mail from {frm} ignored ({msg.get('Subject','')[:40]!r})", flush=True)
                    cursor = uid
                    db.execute("UPDATE mail_cursor SET uid = :u WHERE id = 1", {"u": cursor})
        except Exception as e:  # noqa: BLE001
            print(f"poll hiccup: {type(e).__name__}: {e}", flush=True)
        time.sleep(12)


if __name__ == "__main__":
    raise SystemExit(main())
