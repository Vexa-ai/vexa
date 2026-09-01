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
    if "BEGIN:VEVENT" not in ics:
        return None                       # no event block — never fall back to scanning VTIMEZONE
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
    # ATTENDEE -> participants. PRD §16.2 item 1: the growth atom is the invite, and every
    # attendee on it is an exposed person; the parser used to read ORGANIZER and stop, so the
    # product could only ever speak to the one person who already knew about it.
    parts, seen = [], set()
    for line in re.findall(r"^ATTENDEE[^:]*:(?:mailto:)?(\S+)\s*$", ve, re.I | re.M):
        a = line.strip().lower().rstrip(";,")
        if a and "@" in a and a not in seen:
            seen.add(a)
            parts.append(a)

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
    if start < time.time() - 86400:
        return None                       # a start >24h in the past is a parse artifact (the 1970
                                          # class) or a stale event — never admit it (a bot would
                                          # dispatch IMMEDIATELY on an ancient start)
    return {"organizer": (org.group(1).strip().lower() if org else ""),
            "url": url.group(0), "start": start,
            "ics_uid": (uid.group(1).strip() if uid else f"noid-{int(start)}"),
            "title": (summ.group(1).strip() if summ else "Meeting"),
            "participants": parts,
            "group": group}


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
                    from flows_steps.common import ADMIN_API, ADMIN_KEY, http as _http, scaffolded as _scaff

                    def _known_uid(e: str):
                        code, u = _http("GET", f"{ADMIN_API}/admin/users/email/{e}",
                                        {"X-Admin-API-Key": ADMIN_KEY})
                        return u.get("id") if code == 200 else None

                    decision = route(db, addr, frm, dict(msg.items()), ics, _known_uid, _scaff)
                    if decision is None:
                        print(f"ignored mail from {frm} ({msg.get('Subject','')[:40]!r})", flush=True)
                    else:
                        kind, payload = decision
                        if kind == "invite":
                            n = admit(db, reg, clock, source_event_id=f"ics-{payload['ics_uid']}",
                                      event_type="invite.received", subject_refs=payload)
                            print(f"ICS '{payload['title']}' {payload['organizer']} group={payload['group']} → admitted {n}", flush=True)
                        else:
                            if kind == "new_sender_mail":
                                from flows_steps.common import ensure_platform_user
                                payload["uid"] = ensure_platform_user(frm)
                                print(f"NEW sender {frm} provisioned (uid {payload['uid']})", flush=True)
                            n = admit(db, reg, clock,
                                      source_event_id=f"mail-{msg.get('Message-ID','').strip() or uid}",
                                      event_type="mail.reply",
                                      subject_refs={"uid": str(payload["uid"]), "session": payload["session"],
                                                    "from_addr": frm, "text": strip_quotes(body),
                                                    "subject": msg.get("Subject", ""),
                                                    "orig_msgid": msg.get("Message-ID", "").strip(),
                                                    "organizer": frm})
                            print(f"{kind} {frm} → {payload['session']} → admitted {n}", flush=True)
                    cursor = uid
                    db.execute("UPDATE mail_cursor SET uid = :u WHERE id = 1", {"u": cursor})
        except Exception as e:  # noqa: BLE001
            print(f"poll hiccup: {type(e).__name__}: {e}", flush=True)
        time.sleep(12)


if __name__ == "__main__":
    raise SystemExit(main())
