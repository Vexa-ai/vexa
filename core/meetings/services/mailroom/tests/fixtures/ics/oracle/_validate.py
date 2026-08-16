#!/usr/bin/env python3
"""Validate the mailroom .ics corpus: RFC 5545 line discipline + a real parser.

Asserts what README.md claims: CRLF throughout, no content line over 75 octets,
valid UTF-8, one VEVENT per file, every file parseable by `icalendar` — except
neg-malformed-truncated-vevent.ics, which must NOT parse.

    python3 _validate.py            # exits non-zero on any violation
"""
import pathlib, sys
from icalendar import Calendar

D = pathlib.Path(__file__).resolve().parent
BOT = "mk-dev@dev.vexa.ai"
fail = 0

for p in sorted(D.glob("*.ics")):
    raw = p.read_bytes()
    problems = []
    # CRLF: every LF must be preceded by CR
    for i, b in enumerate(raw):
        if b == 0x0A and (i == 0 or raw[i - 1] != 0x0D):
            problems.append(f"bare LF at byte {i}")
            break
    # no lone CR
    if raw.count(b"\r") != raw.count(b"\r\n"):
        problems.append("lone CR present")
    # 75-octet folding
    for n, line in enumerate(raw.split(b"\r\n"), 1):
        if len(line) > 75:
            problems.append(f"line {n} is {len(line)} octets: {line[:40]!r}")
    # utf-8 decodable
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as e:
        problems.append(f"not utf-8: {e}")

    malformed = p.name == "neg-malformed-truncated-vevent.ics"
    if not malformed:
        if not raw.endswith(b"END:VCALENDAR\r\n"):
            problems.append("does not end with END:VCALENDAR CRLF")
        try:
            cal = Calendar.from_ical(raw)
            ev = [c for c in cal.walk("VEVENT")]
            assert len(ev) == 1, f"{len(ev)} VEVENTs"
            e = ev[0]
            method = str(cal.get("METHOD"))
            uid = str(e["UID"])
            seq = int(e.get("SEQUENCE", 0))
            atts = e.get("ATTENDEE")
            atts = [atts] if atts is not None and not isinstance(atts, list) else (atts or [])
            emails = [str(a).replace("mailto:", "").lower() for a in atts]
            has_bot = BOT in emails
            dts = e["DTSTART"]
            tzid = dts.params.get("TZID")
            rr = e.get("RRULE")
            loc = str(e.get("LOCATION", ""))
            desc = str(e.get("DESCRIPTION", ""))
            xg = str(e.get("X-GOOGLE-CONFERENCE", "")) or str(e.get("X-MICROSOFT-SKYPETEAMSMEETINGURL", ""))
            url_found = ("meet.google.com" in (loc + desc + xg)) or ("teams.microsoft.com" in (loc + desc + xg))
            print(f"{p.name:44s} METHOD={method:8s} SEQ={seq} bot={'Y' if has_bot else 'N'} "
                  f"tz={tzid or '-':26s} rrule={'Y' if rr else 'N'} url={'Y' if url_found else 'N'} "
                  f"att={len(emails)} uid={uid[:22]}…")
        except Exception as exc:
            problems.append(f"parse failed: {type(exc).__name__}: {exc}")
    else:
        try:
            Calendar.from_ical(raw)
            problems.append("MALFORMED FIXTURE PARSED CLEANLY — it should not")
        except Exception as exc:
            print(f"{p.name:44s} correctly unparseable: {type(exc).__name__}")

    if problems:
        fail += 1
        for pr in problems:
            print(f"   !! {p.name}: {pr}")

print("\nFAILURES:", fail)
sys.exit(1 if fail else 0)
