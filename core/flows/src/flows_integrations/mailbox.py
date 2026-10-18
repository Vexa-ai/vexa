"""mailbox integration — its own process: the REAL inbox becomes facts.

  ICS attachment (with a meeting link)   → invite.received   (dedup: UID + occurrence)
  reply on a registered thread           → mail.reply        (dedup: inbound Message-ID)
  anything else                          → logged, ignored

WHO WE WILL ACT FOR is decided BEFORE any of that, in `flows_integrations/mail_policy`, and it is
the first question this file asks (R-B12). A sender who is neither a known user nor inside the
deployment's own domain allow-list gets no account, no agent turn and no model call — one
quarantine row, and at most one fixed line. Two rate limits, per-sender and global, bound what the
inbox can cost even when every sender is legitimate.

Threading is the law here: a reply routes by In-Reply-To/References looked up in mail_thread —
never by sender. Cursor is durable (mail_cursor row) — restarts resume, never re-admit.

WHICH inbox this is stops at `inbox.get_inbox()` — routing, admission and dedup below are
source-blind by design:

    VEXA_MAIL_INBOX = imap     (default) — IMAP against imap.gmail.com, behaviour unchanged
                    = mailpit           — the dev stack's mail double (REST only, no IMAP)
    VEXA_MAILPIT_URL           — mailpit's HTTP base (default http://127.0.0.1:8025)
    VEXA_MAIL_ADDR             — the address this inbox answers as; mailpit filters on it
    VEXA_MAILPIT_LOOKBACK_S    — re-scan window behind the watermark (default 300)

Every one of those, and the mail-policy keys beside them, is declared in `flows_config.DECLARED`.

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

import flows_config as cfg
from flows import Registry, SystemClock, admit, postgres_db
from flows_defs import production
from flows_integrations import mail_policy, outlook
from flows_integrations.inbox import get_inbox
from flows_steps.common import db_url

POLL_SECONDS = 12

# A meeting link is Meet, Zoom, Teams or Jitsi — the SAME four platforms the rest of the product
# accepts (`collector/meeting_link.py`). Meet stays byte-for-byte the pattern it always was (case
# sensitive, lowercase code); Zoom is the recorded corpus's platform — those invites are
# zoom.us.
#
# Teams and Jitsi were missing, and the way they failed is the point: an invite for a platform
# this function does not know is not refused, it is IGNORED — `parse_ics` returns None, `route`
# returns None, and the poller logs "ignored mail from …". Nothing reaches the person who
# forwarded it and nothing reaches us. It was found on the second-invite path, where forwarding
# an invite IS the mechanism: a person is told "forward the invite and Vexa joins", they do, and
# for two of the four supported platforms nothing whatsoever happens. Teams is the one that
# matters most commercially — it is what a bank or a studio actually runs.
MEET_URL = re.compile(r"https://meet\.google\.com/[a-z-]+")
ZOOM_URL = re.compile(r"https://(?:[A-Za-z0-9-]+\.)*zoom\.us/j/\d+(?:\?pwd=[A-Za-z0-9._%~-]+)?")
# `\\` and `;` EXCLUDED from the class, and that is a fix rather than tidiness: a newline inside
# an ICS TEXT value arrives as the TWO characters backslash-n, so a class that permits `\\` runs
# straight past the end of the link into the next line's prose — the captured URL reads
# `…?p=HspUEOnQK2jxCFVGhg\\nMeeting`, which joins nothing. Measured on a live Microsoft 365
# invitation (Vexa-ai/vexa#1320); a `;` ends an ICS parameter list and never belongs either.
TEAMS_URL = re.compile(
    r"https://teams\.(?:microsoft|live)\.com/l/meetup-join/[^\s<>\"';\\]+"
    r"|https://teams\.(?:microsoft|live)\.com/meet/[^\s<>\"';\\]+")
# Jitsi is host-scoped exactly as meeting_link.py scopes it: meet.jit.si always, plus whatever
# VEXA_JITSI_HOSTS declares. A bare "any host with a path" rule would match half the web.
_JITSI_HOSTS = [h.strip() for h in
                ("meet.jit.si," + os.environ.get("VEXA_JITSI_HOSTS", "")).split(",") if h.strip()]
JITSI_URL = re.compile(
    r"https://(?:" + "|".join(re.escape(h) for h in _JITSI_HOSTS) + r")/[^\s<>\"'?#]+")


# WINDOWS ZONE NAMES. Outlook and Exchange write `TZID:W. Europe Standard Time`, not
# `Europe/Berlin` — the CLDR "windowsZones" spelling, which `zoneinfo` has never heard of. Before
# this table `ZoneInfo(...)` raised `ZoneInfoNotFoundError` straight out of `parse_ics`, out of
# `route`, and out of the mailbox poll: EVERY Exchange invite was dropped, by an exception rather
# than by a decision, so nothing downstream could say which meeting had gone missing. The pilot's
# organiser sends from Exchange, and `W. Europe Standard Time` is the row it lands on.
#
# Not the whole CLDR mapping — the zones a real invite to us arrives in, and the fallback below
# handles the rest without a dependency. Each maps to the IANA zone CLDR itself names for it.
_WINDOWS_ZONES = {
    "w. europe standard time": "Europe/Berlin",
    "central europe standard time": "Europe/Budapest",
    "central european standard time": "Europe/Warsaw",
    "romance standard time": "Europe/Paris",
    "gmt standard time": "Europe/London",
    "greenwich standard time": "Atlantic/Reykjavik",
    "e. europe standard time": "Europe/Chisinau",
    "fle standard time": "Europe/Kiev",
    "gtb standard time": "Europe/Bucharest",
    "turkey standard time": "Europe/Istanbul",
    "israel standard time": "Asia/Jerusalem",
    "russian standard time": "Europe/Moscow",
    "utc": "UTC",
    "eastern standard time": "America/New_York",
    "central standard time": "America/Chicago",
    "mountain standard time": "America/Denver",
    "pacific standard time": "America/Los_Angeles",
    "arabian standard time": "Asia/Dubai",
    "india standard time": "Asia/Kolkata",
    "se asia standard time": "Asia/Bangkok",
    "china standard time": "Asia/Shanghai",
    "singapore standard time": "Asia/Singapore",
    "korea standard time": "Asia/Seoul",
    "tokyo standard time": "Asia/Tokyo",
    "aus eastern standard time": "Australia/Sydney",
    "new zealand standard time": "Pacific/Auckland",
    "south africa standard time": "Africa/Johannesburg",
    "e. south america standard time": "America/Sao_Paulo",
}

# THE PER-MEETING OPT-OUT (PRD §16.2 item 3). Creator-controlled sharing is default ON — that one
# value is the viral coefficient — and the creator's way to exclude ONE meeting is a token in the
# invite, the only surface a meeting's creator owns without an administrator. Same shape as the
# `#group:` token this parser already scans for, and scanned over the whole ICS for the same
# reason: people put it in the title as readily as in the body.
#
# `\b` on BOTH sides, so `#noshareholders` is a hashtag about shareholders and not a silently
# suppressed fan-out — a token that fires on a substring produces the one failure nobody reports.
NOSHARE = re.compile(r"#noshare\b", re.I)


def _zone(tzid: str):
    """The IANA zone for an ICS `TZID`, or None when we cannot name it.

    Three tries, cheapest first: the name as given (Google sends IANA), the Windows table above,
    and nothing. NEVER raises — see `parse_ics`, where None means "treat this DTSTART as UTC",
    which is exactly what the floating-DTSTART branch beside it already does with the same
    uncertainty. A meeting an hour off still joins; an invite that raises never joins at all."""
    from zoneinfo import ZoneInfo
    # THE QUOTES ARE MICROSOFT'S AND BELONG TO NEITHER NAME. Outlook writes
    # `DTSTART;TZID="W. Europe Standard Time":…` as readily as the bare form, and with the quotes
    # still attached this lookup misses the table, misses tzdata, and falls through to UTC — an
    # hour wrong, silently, on the pilot's own zone, which is the one failure shape this table
    # exists to prevent. Stripped here rather than at the regex so both spellings reach one rule.
    tzid = (tzid or "").strip().strip('"').strip("'")
    for name in (tzid, _WINDOWS_ZONES.get(tzid.strip().lower(), "")):
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 — an unknown zone is a fact we lack, not a failure
            continue
    return None


def _meeting_url(text: str) -> str | None:
    for pat in (MEET_URL, ZOOM_URL, TEAMS_URL, JITSI_URL):
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def _unfold(ics: str) -> str:
    """RFC 5545 line folding: a CRLF followed by one space or tab continues the line. Real
    invites fold — a Zoom URL with a `?pwd=` is long enough to be split mid-token, and an
    ATTENDEE line with a CN and a mailto is longer still.

    The implementation lives in `flows_integrations.outlook`, because every Microsoft reading
    rule there depends on it and it must not exist twice."""
    return outlook.unfold_ics(ics)


def parse_ics(ics: str, self_addr: str | None = None) -> dict | None:
    ics = _unfold(ics)
    if "BEGIN:VEVENT" not in ics:
        return None                       # no event block — never fall back to scanning VTIMEZONE
    ve = ics.split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0]
    # MICROSOFT'S OWN PROPERTIES FIRST, and the order is the whole point (see
    # `flows_integrations/outlook.py`, rules 4-6). The generic scan reads the event
    # top-to-bottom, and on a real Exchange invite that means LOCATION — the literal string
    # "Microsoft Teams Meeting", carrying no URL at all — and then DESCRIPTION, whose FIRST link
    # is the short form `teams.microsoft.com/meet/<digits>`. That is A DIFFERENT IDENTIFIER FOR
    # THE SAME MEETING than `X-MICROSOFT-SKYPETEAMSMEETINGURL` carries, and nothing anywhere
    # errors: the invite is admitted, a bot is dispatched, and it joins nothing.
    #
    # An invite with no Microsoft properties is untouched — `teams_join_url` returns None and the
    # scan below is byte-for-byte what it was.
    url = outlook.teams_join_url(ics) or _meeting_url(ve) or _meeting_url(ics)
    if not url:
        return None
    # ⚠ ANCHORED TO A LINE START, and it has to be. These patterns are case-insensitive and
    # `[^:]*` matches newlines, so an UNANCHORED `ORGANIZER` matched the word wherever it appeared
    # — inside a UID, a SUMMARY, a DESCRIPTION — and then ate greedily to the NEXT colon anywhere
    # in the event, capturing whatever followed it. Found 2026-09-02 on a rehearsal invite whose
    # UID carried the state name: the organizer parsed as `20260902t183213z`, the DTSTAMP off the
    # following line, and `rsvp_accept` mailed it — `SMTPRecipientsRefused: 553 not a valid RFC
    # 5321 address`.
    #
    # It fails LOUDLY here only because our own mail double refuses the address. A real ICS whose
    # SUMMARY reads "Organizer sync" would have handed the flow a plausible-looking wrong address
    # instead, and every touch for that meeting would have gone to a stranger — silently, with the
    # flow reporting success. Property names live at the start of a content line (RFC 5545 §3.1),
    # so `re.M` + `^` is the whole fix, and `_unfold` above has already joined continuations.
    org = re.search(r"^ORGANIZER[^:]*:(?:mailto:)?([^\s]+)", ve, re.I | re.M)
    # `"…"` in the TZID alternation: Outlook writes `DTSTART;TZID="W. Europe Standard Time":…`
    # and `[^:;]+` alone stops at neither quote — the captured name kept them, and `_zone` then
    # missed a table row it has. Both spellings now reach `_zone`, which strips them.
    dt = re.search(r'^DTSTART(?:;TZID=("[^"]*"|[^:;]+))?[^:]*:(\d{8}T\d{6})(Z?)', ve, re.M)
    uid = re.search(r"^UID:(.+)$", ve, re.M)
    # THE OCCURRENCE, not the series. RFC 5545 gives every occurrence of a recurring event the
    # SAME `UID`; only `RECURRENCE-ID` separates them. The dedup key was `ics-<UID>`, so a
    # recurring meeting was recorded exactly once, ever — every later occurrence was silently a
    # duplicate, produced no reaction and no error, on precisely the "put the mailbox on the
    # recurring dailies" case `POST /events/batch` exists for (R-B02).
    rec = re.search(r"^RECURRENCE-ID(?:;[^:]*)?:(\S+)$", ve, re.M)
    # `;LANGUAGE=en-US` — Exchange puts a parameter on SUMMARY where Google does not, so
    # `^SUMMARY:` matched nothing and every Exchange invite was titled "Meeting". Silent, and it
    # reaches the person: the title is the subject line of the mail we send back. Same
    # `(?:;[^:\n]*)?` shape RECURRENCE-ID above already uses.
    summ = re.search(r"^SUMMARY(?:;[^:\n]*)?:(.+)$", ve, re.M)
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
    # THE DISPLAY NAMES, alongside the addresses. An ATTENDEE line carries `CN="Anna Smith"`, and
    # that is the only place a person's real name and their address appear together — without it,
    # matching a transcript's speaker label to somebody on the invite means guessing a name out of
    # an email local part, which is exactly the guess we must not ship. Address -> name, addresses
    # lowercased to match `participants`, names kept verbatim.
    #
    # A separate ref, not a change to `participants`: every existing consumer of that list keeps
    # the shape it has, and a CN that is missing is simply an address with no name here.
    names: dict = {}
    for a in re.finditer(r"ATTENDEE([^\n]*?)mailto:([^\s;,>\"]+)", ve, re.I):
        params, who = a.group(1), a.group(2).strip().lower()
        if not who or who == me:
            continue
        if who not in participants:
            participants.append(who)
        cn = re.search(r'CN=(?:"([^"]*)"|([^;:,]*))', params, re.I)
        label = ((cn.group(1) or cn.group(2)) if cn else "").strip()
        if label and who not in names:
            names[who] = label
    start = time.time() + 150
    if dt:
        import calendar as cal
        from datetime import datetime
        # `\d{8}T\d{6}` IS NOT ENOUGH TO MAKE strptime SAFE. Its `%H`/`%m`/`%d` patterns each
        # accept a single digit, so a value the regex admits can still be unparseable —
        # `20301231T240000` raises `ValueError: unconverted data remains: 0`. Out of `parse_ics`,
        # out of `route`, out of the poll: the cursor advances only after a message is routed, so
        # ONE such invite wedges every message behind it, forever. Exactly the failure `_zone`
        # was written to end, one line further down, arriving by a different door. Found by the
        # mutation storm over the Outlook corpus (`tests/test_exchange_ics.py`).
        #
        # An unreadable DTSTART is treated as an ABSENT one — the `if dt:` branch is simply not
        # taken — because that is the answer this parser already gives to the same amount of
        # information, and adding a second policy for it would be the drift, not the fix.
        try:
            t = time.strptime(dt.group(2), "%Y%m%dT%H%M%S")
        except ValueError:
            t = None
    if dt and t is not None:
        if dt.group(3) == "Z":
            start = cal.timegm(t)
        elif dt.group(1) and (tz := _zone(dt.group(1))) is not None:
            start = datetime(*t[:6], tzinfo=tz).timestamp()
        else:
            # A TZID WE CANNOT NAME IS TREATED AS UTC, never as an error (the Exchange incident —
            # see `_WINDOWS_ZONES`). Falling through to the same branch as a floating DTSTART is
            # deliberate: both are "a wall-clock time whose zone we do not have", and both answer
            # it the same way rather than growing a second policy for the same missing fact.
            # A FLOATING DTSTART IS UTC, never the server's local time (R-B10). `time.mktime`
            # reads the tuple in whatever zone the worker happens to run in, and this value drives
            # the bot dispatch and the note filename — the two things that must not move when the
            # process does. Every other clock in this area is already guarded against exactly this.
            start = cal.timegm(t)
    if start < time.time() - 86400:
        return None                       # a start >24h in the past is a parse artifact (the 1970
                                          # class) or a stale event — never admit it (a bot would
                                          # dispatch IMMEDIATELY on an ancient start)
    return {"organizer": (org.group(1).strip().lower() if org else ""),
            "url": url, "start": start,
            "ics_uid": (uid.group(1).strip() if uid else f"noid-{int(start)}"),
            # what makes THIS occurrence not the series: RECURRENCE-ID when the sender gave one,
            # else the occurrence's own DTSTART, which is what a per-occurrence invite differs by.
            "occurrence": (rec.group(1).strip() if rec else (dt.group(2) + dt.group(3) if dt else "")),
            "title": (summ.group(1).strip() if summ else "Meeting"),
            "group": group,
            "participants": participants,
            "participant_names": names,
            # TRUTHY, and only present when it is true. Admission's `_merge_refs` keeps an
            # existing key unless its value is falsy and the incoming one is not — so a
            # `share: False` could be silently flipped to True by any later admission for the
            # same meeting, which is a suppressed fan-out un-suppressing itself. This spelling
            # cannot be clobbered by that rule in either direction.
            **({"share_opt_out": True} if NOSHARE.search(ics) else {})}


def route(db, self_addr: str, frm: str, headers: dict, ics: str | None,
          known_uid, is_scaffolded, allowed: set | None = None) -> tuple[str, dict] | None:
    """The routing DECISION, pure: returns (kind, payload) or None (ignore).

      kind ∈ invite | invite_quarantine | thread_reply | known_user_mail | new_sender_mail
             | quarantine

    `known_uid(email) -> uid|None` and `is_scaffolded(uid) -> bool` are injected so the routing
    storm can drive every branch with no services; `allowed` is the domain allow-list, likewise
    injectable, defaulting to `mail_policy.allow_domains(self_addr)`.

    TWO AUTHORIZATION RULES LIVE HERE, and both were absent (R-B12):

    1. **A stranger is not a customer.** An address that is neither a known user nor inside the
       deployment's own domain gets `quarantine` — no account, no agent turn, no model call. It
       used to get all three. PRD §16.2, pointed inward: outside the domain, never.

    2. **`In-Reply-To` is an id, not an identity.** The thread row is still the only thing that
       decides WHICH conversation a reply belongs to — routing by sender remains the
       wrong-mail-answered-onboarding bug and is not coming back. What changed is that the ref no
       longer carries the sender INTO that conversation: the thread's own subject may continue it,
       and anybody else falls through to their own identity, where rule 1 meets them. Before this,
       a forged `In-Reply-To` — the message id is in the headers of every mail we send — ran an
       agent turn inside a stranger's session, on a stranger's workspace, with the forger's text.

    An INVITE is the same question asked of the ORGANIZER: an invite from an organizer we cannot
    place records the meeting facts and creates nothing (see `handle`).
    """
    if not frm or frm == self_addr:
        return None
    allowed = mail_policy.allow_domains(self_addr) if allowed is None else allowed
    if ics and "BEGIN:VEVENT" in ics:
        if "METHOD:REPLY" in ics or "METHOD:CANCEL" in ics:
            return None               # calendar machinery (our own RSVP echoes!) — never a
                                      # conversation, never a provisioning trigger (storm catch:
                                      # we nearly onboarded calendar-notification@google.com)
        ev = parse_ics(ics, self_addr)
        if not (ev and ev["organizer"]):
            return None
        org = ev["organizer"]
        if known_uid(org) is not None or mail_policy.in_allow_list(org, self_addr, allowed):
            return ("invite", ev)
        return ("invite_quarantine", ev)
    auto = (headers.get("Auto-Submitted", "no").lower() != "no"
            or headers.get("Precedence", "").lower() in ("bulk", "list", "junk")
            or any(t in frm for t in ("no-reply", "noreply", "mailer-daemon", "postmaster", "bounce")))
    ref = (headers.get("In-Reply-To") or "").strip() or \
          (headers.get("References", "").split() or [""])[-1]
    hit = db.execute("SELECT subject_uid, session FROM mail_thread WHERE message_id = :m",
                     {"m": ref}) if ref else []
    forged = False
    if hit:
        suid, session = hit[0]
        uid = known_uid(frm)
        if uid is not None and str(uid) == str(suid):
            return ("thread_reply", {"uid": suid, "session": session})
        # Not this thread's participant. The ref buys nothing; the sender is judged on their own
        # identity below, and the quarantine row that may follow says the ref was the reason.
        forged = True
    if auto:
        return None
    uid = known_uid(frm)
    if uid is not None:
        return ("known_user_mail", {"uid": str(uid),
                                    "session": "main" if is_scaffolded(str(uid)) else "onboarding"})
    if mail_policy.in_allow_list(frm, self_addr, allowed):
        return ("new_sender_mail", {"session": "onboarding"})
    return ("quarantine", {
        "kind": mail_policy.THREAD_MISMATCH if forged else mail_policy.STRANGER_MAIL,
        "reason": (f"{frm} is not a user and not inside the mail allow-list "
                   f"({', '.join(sorted(allowed)) or 'empty'})"
                   + (f"; its In-Reply-To named thread {ref} it is not a participant of"
                      if forged else ""))})


def invite_source_id(ev: dict) -> str:
    """The dedup key for one INVITE — the occurrence, never the series (R-B02).

    `ics-<UID>` recorded a recurring meeting exactly once and then swallowed every later
    occurrence in silence. RFC 5545 repeats the `UID` for the whole series, so the key needs the
    occurrence too: `RECURRENCE-ID` when the sender sent one, else the occurrence's own `DTSTART`
    — which is what two per-occurrence invites actually differ by. An event with neither keeps the
    old key exactly, so nothing that used to dedup stops deduping."""
    occ = str(ev.get("occurrence") or "").strip()
    return f"ics-{ev['ics_uid']}-{occ}" if occ else f"ics-{ev['ics_uid']}"


#: WHAT THE ELISION LOOKS LIKE when a body is longer than the cap. It is part of the text a reader
#: — a person, or an agent reading the untrusted block — actually sees, on purpose: a body that was
#: silently cut ends mid-sentence and reads as a message that ends mid-sentence, which is a
#: different fact about the sender. `{n}` is how many characters were dropped.
BODY_ELISION = "\n\n[… {n} more characters of this message elided at VEXA_FLOWS_MAIL_BODY_MAX ({cap}) …]"


def body_max() -> int:
    """How much of an inbound body may travel on `mail.reply` — `VEXA_FLOWS_MAIL_BODY_MAX`.

    THE KEY WAS DECLARED AND READ BY NOTHING (R-B12). `flows_config.DECLARED` carries it with a
    default of 4000 and the sentence *"how much of an inbound body may enter an agent prompt,
    inside the untrusted block"*; `core/flows/README.md` tells an operator that an allowed body
    "arrives quoted, fenced, length-capped (`VEXA_FLOWS_MAIL_BODY_MAX`)". Neither was true: the
    only cap in the intake was a literal `[:2000]` on the line below, so an operator who set the
    key got no error and no effect, and one who read the README believed a number that was not the
    number. That is the failure mode `test_config_declaration`'s declared⊆read direction exists to
    catch, and it caught this one.

    Read PER CALL, never bound at import: the poller is a long-lived process and a door resolved at
    import is the defect the rest of this brick spent a release removing.

    Floored at 1: a cap of zero would admit a reply carrying no text at all, which is a fact
    thrown away rather than a limit applied. `cfg.get_int` already answers the declared default for
    a value that is not a number, so a typo is 4000 and never a crash mid-poll."""
    return max(cfg.get_int("VEXA_FLOWS_MAIL_BODY_MAX"), 1)


def strip_quotes(body: str) -> str:
    """The sender's own words: quoted lines dropped, then capped at `body_max()`.

    The quote strip comes FIRST and the cap second, which is the order that matters — a reply whose
    first screen is the thread it is answering would otherwise spend its whole budget on text we
    already sent."""
    text = "\n".join(l for l in body.strip().splitlines() if not l.strip().startswith(">"))
    cap = body_max()
    if len(text) <= cap:
        return text
    return text[:cap] + BODY_ELISION.format(n=len(text) - cap, cap=cap)


def _fixed_reply(db, msg, ext_id: str, now: float, reply) -> bool:
    """AT MOST one fixed line back to a quarantined stranger — a template, never a model.

    Off unless the deployment turns it on, and once per address ever. Silence is the safest answer
    to somebody we cannot place: an automatic reply to an unverified sender is a reflector, and two
    auto-responders that answer each other are a mail loop nobody notices until the bill.
    """
    if reply is None or not cfg.get_bool("VEXA_FLOWS_MAIL_QUARANTINE_REPLY"):
        return False
    if mail_policy.already_answered(db, msg.frm):
        return False
    try:
        reply(msg.frm, "Re: " + (msg.subject or "your message"), mail_policy.QUARANTINE_TEMPLATE)
    except Exception as e:  # noqa: BLE001 — a refusal we could not post is still a refusal
        print(f"quarantine reply to {msg.frm} failed: {type(e).__name__}: {e}", flush=True)
        return False
    mail_policy.mark_answered(db, ext_id, now)
    return True


def handle(db, reg, clock, self_addr: str, msg, known_uid, is_scaffolded,
           provision, reply=None) -> tuple[str, int] | None:
    """One inbound message → at most one admission. Source-blind and service-blind: every reach
    outside is injected, so the whole intake is drivable offline.

    THREE THINGS CAN NOW HAPPEN INSTEAD OF AN ADMISSION, and each one is a row rather than a
    silence: a stranger's mail is quarantined, an invite from an organizer we cannot place is
    quarantined WITH ITS MEETING FACTS, and a mail that would exceed a rate limit is quarantined
    with the count that stopped it. `provision` — the call that mints a platform account — is
    reached only on the `new_sender_mail` branch, which now requires the allow-list.
    """
    decision = route(db, self_addr, msg.frm, msg.headers, msg.ics, known_uid, is_scaffolded)
    if decision is None:
        return None
    kind, payload = decision
    ext = str(msg.message_id or msg.ext_id)
    now = clock.now()
    if kind == "invite":
        n = admit(db, reg, clock, source_event_id=invite_source_id(payload),
                  event_type="invite.received", subject_refs=payload)
        return ("invite", n)
    if kind == "invite_quarantine":
        # THE FACTS SURVIVE THE REFUSAL. Decision 19 binds the prepare touch to the organizer and
        # to attendees who are already users, and says the workspace is established on the click,
        # "never for someone who never clicks". An organizer this deployment cannot place has not
        # clicked anything: minting them an account, RSVPing in their calendar and mailing them
        # would be the pre-meeting fan-out that decision explicitly cut. So nothing is created and
        # nothing is sent — and the meeting the invite described is kept verbatim in the row, so a
        # known user can have it re-admitted through the operator's `POST /events` once the
        # organizer is vouched for, without anyone going back to the mailbox.
        mail_policy.quarantine(
            db, ext_id=ext, frm=msg.frm, kind=mail_policy.UNVERIFIED_INVITE,
            reason=(f"invite organizer {payload['organizer']} is not a user and not inside the "
                    f"mail allow-list — meeting facts recorded, no account, no touch"),
            facts=payload, at=now)
        return ("invite_quarantine", 0)
    if kind == "quarantine":
        mail_policy.quarantine(db, ext_id=ext, frm=msg.frm, kind=payload["kind"],
                               reason=payload["reason"],
                               facts={"subject": msg.subject}, at=now)
        _fixed_reply(db, msg, ext, now, reply)
        return ("quarantine", 0)
    # ── from here on the sender may cause an AGENT TURN, so the ceilings apply ──────────────
    limited = mail_policy.rate_check(db, msg.frm, now)
    if limited:
        mail_policy.quarantine(db, ext_id=ext, frm=msg.frm, kind=mail_policy.RATE_LIMITED,
                               reason=limited, facts={"subject": msg.subject}, at=now)
        return ("rate_limited", 0)
    if kind == "new_sender_mail":
        payload["uid"] = provision(msg.frm)
    n = admit(db, reg, clock,
              source_event_id=f"mail-{msg.message_id or msg.ext_id}",
              event_type="mail.reply",
              subject_refs={"uid": str(payload["uid"]), "session": payload["session"],
                            "from_addr": msg.frm, "text": strip_quotes(msg.body),
                            "subject": msg.subject, "orig_msgid": msg.message_id,
                            "organizer": msg.frm})
    if n:
        mail_policy.record_turn(db, ext, msg.frm, now)
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

    from flows_steps.common import ensure_platform_user, platform_user_id
    from flows_steps.common import scaffolded as _scaff
    from flows_steps.notify import notify

    # THROUGH THE SHARED LOOKUP, not a second spelling of it. This used to build the url itself,
    # interpolating an address that comes off an ICS ATTENDEE line straight into the path (R-B14);
    # `platform_user_id` percent-encodes it and is the one place the question is asked.
    def _known_uid(e: str):
        return platform_user_id(e) or None

    def _reply(to: str, subject: str, body: str) -> str:
        return notify(to, subject, body)

    while True:
        try:
            self_addr = inbox.address().lower()
            for msg in inbox.fetch(cursor):
                out = handle(db, reg, clock, self_addr, msg, _known_uid, _scaff,
                             ensure_platform_user, reply=_reply)
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
