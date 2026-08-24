"""mailbox integration — its own process: the REAL inbox becomes facts.

  ICS attachment (with a meeting link)   → invite.received   (dedup: the ICS UID)
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
from flows_integrations.meeting_link import find_in_ics, unfold
from flows_steps.common import db_url


#: Windows timezone name → IANA key (CLDR ``windowsZones``, the territory-001 default row).
#:
#: Exchange writes WINDOWS names into ``TZID`` — ``W. Europe Standard Time``, not
#: ``Europe/Vienna`` — and ``zoneinfo.ZoneInfo`` raises ``ZoneInfoNotFoundError`` on every one of
#: them. That exception escapes ``parse_ics``, so before this table a single Outlook invite took
#: the mailbox poller down mid-batch: not a wrong start time, no mail after it processed. Google
#: invites never exercised it because Google writes IANA keys.
#:
#: Only the mapping is here; the offsets and DST rules stay tzdata's. An unmapped Windows name is
#: REFUSED (below) rather than guessed — a bot dispatched an hour off looks like a product failure,
#: where a refusal is a precise one.
#:
#: ⚠️ SUPERSEDED BY Vexa-ai/vexa#1318 — DELETE THIS BLOCK WHEN THAT MERGES. That PR moves
#: ``parse_ics`` out of this module into ``flows_integrations/ics.py`` and brings the FULL CLDR
#: ``windowsZones`` territory-001 table plus quoted-TZID and UTF-16LE handling — strictly better
#: than this subset, which exists only because the Teams work sits on the shared base branch and
#: an Outlook invite otherwise takes the poller down before the platform logic is ever reached.
#: On merge: drop ``_WINDOWS_TZ`` and ``_zone`` here and keep #1318's; the Teams change itself is
#: three lines inside ``ics.parse_ics`` (the ``find_in_ics`` call and the four extra facts).
_WINDOWS_TZ = {
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "Romance Standard Time": "Europe/Paris",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "GTB Standard Time": "Europe/Bucharest",
    "E. Europe Standard Time": "Europe/Chisinau",
    "FLE Standard Time": "Europe/Kiev",
    "Russian Standard Time": "Europe/Moscow",
    "Turkey Standard Time": "Europe/Istanbul",
    "Israel Standard Time": "Asia/Jerusalem",
    "Arabian Standard Time": "Asia/Dubai",
    "Arab Standard Time": "Asia/Riyadh",
    "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai",
    "Singapore Standard Time": "Asia/Singapore",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "New Zealand Standard Time": "Pacific/Auckland",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "US Mountain Standard Time": "America/Phoenix",
    "Pacific Standard Time": "America/Los_Angeles",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Canada Central Standard Time": "America/Regina",
    "SA Pacific Standard Time": "America/Bogota",
    "SA Eastern Standard Time": "America/Cayenne",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Argentina Standard Time": "America/Buenos_Aires",
    "South Africa Standard Time": "Africa/Johannesburg",
    "W. Central Africa Standard Time": "Africa/Lagos",
    "E. Africa Standard Time": "Africa/Nairobi",
    "Egypt Standard Time": "Africa/Cairo",
    "Morocco Standard Time": "Africa/Casablanca",
    "UTC": "UTC",
}


def _zone(tzid: str):
    """``TZID`` → a tzinfo, or None when the name resolves to nothing we trust.

    Tries IANA first (Google, Apple, most CalDAV), then the Windows table (Exchange). Never
    raises: an unresolvable zone is a data fact about someone else's calendar server, and the
    poller must survive it."""
    from zoneinfo import ZoneInfo
    for key in (tzid, _WINDOWS_TZ.get(tzid, "")):
        if not key:
            continue
        try:
            return ZoneInfo(key)
        except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError, and anything tzdata throws
            continue
    return None


def parse_ics(ics: str) -> dict | None:
    ics = unfold(ics or "")               # RFC 5545 §3.1 — Outlook folds at 75 octets, and every
                                          # regex below would otherwise match half a value
    if "BEGIN:VEVENT" not in ics:
        return None                       # no event block — never fall back to scanning VTIMEZONE
    ve = ics.split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0]
    # Platform-agnostic link extraction (meeting_link.find_in_ics — rules mirrored from the
    # product's collector/meeting_link.py). An UNSUPPORTED platform is still returned: the
    # organizer is owed "I can't join Zoom yet", not silence.
    link = find_in_ics(ics, ve)
    if link is None:
        return None
    org = re.search(r"ORGANIZER[^:]*:(?:mailto:)?([^\s]+)", ve, re.I)
    dt = re.search(r"DTSTART(?:;TZID=([^:;]+))?[^:]*:(\d{8}T\d{6})(Z?)", ve)
    # Property PARAMETERS are not optional in practice: Exchange writes SUMMARY;LANGUAGE=en-US
    # and UID with none, Google writes both bare. A name-anchored `^NAME:` misses every
    # localized Outlook property and silently titled real bank invites "Meeting".
    uid = re.search(r"^UID(?:;[^:\n]*)?:(.+)$", ve, re.M)
    summ = re.search(r"^SUMMARY(?:;[^:\n]*)?:(.+)$", ve, re.M)
    group = None
    gm = re.search(r"#group:([\w-]+)", ics)
    if gm:
        group = gm.group(1)
    start = time.time() + 150
    if dt:
        import calendar as cal
        from datetime import datetime
        t = time.strptime(dt.group(2), "%Y%m%dT%H%M%S")
        if dt.group(3) == "Z":
            start = cal.timegm(t)
        elif dt.group(1):
            zone = _zone(dt.group(1))
            if zone is None:
                return None               # an unresolvable TZID is refused, never guessed — a bot
                                          # dispatched an hour off reads as a product failure
            start = datetime(*t[:6], tzinfo=zone).timestamp()
        else:
            start = time.mktime(t)
    if start < time.time() - 86400:
        return None                       # a start >24h in the past is a parse artifact (the 1970
                                          # class) or a stale event — never admit it (a bot would
                                          # dispatch IMMEDIATELY on an ancient start)
    return {"organizer": (org.group(1).strip().lower() if org else ""),
            "url": link.url, "start": start,
            # The addressing key the gateway stores and every later lookup uses. Carried as
            # FACTS on invite.received so dispatch_bot never re-derives an id from the URL
            # shape (the old `url.rsplit("/", 1)[1]` was Meet-only by construction).
            "platform": link.platform,
            "native_meeting_id": link.native_meeting_id,
            "passcode": link.passcode,
            "platform_supported": link.supported,
            "ics_uid": (uid.group(1).strip() if uid else f"noid-{int(start)}"),
            "title": (summ.group(1).strip() if summ else "Meeting"),
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
                            print(f"ICS '{payload['title']}' {payload['organizer']} "
                                  f"{payload['platform']}:{payload['native_meeting_id']}"
                                  f"{'' if payload['platform_supported'] else ' (UNSUPPORTED)'} "
                                  f"group={payload['group']} → admitted {n}", flush=True)
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
