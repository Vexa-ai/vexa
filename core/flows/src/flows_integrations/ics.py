"""ICS parsing — Google AND Outlook/Exchange shapes.

Exchange differs from Google in three ways that each broke a naive parser:

1. **Folded lines.** RFC 5545 §3.1 allows a line break followed by one space/tab anywhere;
   Outlook folds at 75 octets *hard*, so a Meet URL, a UID or an ORGANIZER address routinely
   arrives split across two lines. Unfold FIRST or the regexes match half a value.
2. **Windows timezone names.** Outlook writes `TZID:"W. Europe Standard Time"` (often quoted),
   not an IANA zone. `zoneinfo.ZoneInfo("W. Europe Standard Time")` raises — and an exception in
   the parser stalls the mailbox cursor forever. We map Windows → IANA (CLDR table below) and
   fall back to a floating time rather than throwing.
3. **Encodings.** Exchange attaches `text/calendar` as base64 and, on some connectors, UTF-16LE
   with a BOM. `decode_ics()` sniffs the BOM before falling back to UTF-8.

The whole module is stdlib-only and pure: no I/O, no clock beyond `time.time()`.
"""
from __future__ import annotations

import re
import time

# ---------------------------------------------------------------------------------------------
# Windows → IANA timezone map.
#
# Source: Unicode CLDR `common/supplemental/windowsZones.xml`, the `territory="001"` (world
# default) rows — https://github.com/unicode-org/cldr/blob/main/common/supplemental/windowsZones.xml
# (CLDR release 45, Unicode license). Only the default territory row is vendored: the per-country
# rows exist to pick a *representative* city inside the same offset+DST rule, so the world default
# gives the correct instant for every one of them. Vendored as a dict rather than pulled from a
# package because this file must stay stdlib-pure.
# ---------------------------------------------------------------------------------------------
WINDOWS_TO_IANA: dict[str, str] = {
    "Dateline Standard Time": "Etc/GMT+12",
    "UTC-11": "Etc/GMT+11",
    "Aleutian Standard Time": "America/Adak",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Marquesas Standard Time": "Pacific/Marquesas",
    "Alaskan Standard Time": "America/Anchorage",
    "UTC-09": "Etc/GMT+9",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "UTC-08": "Etc/GMT+8",
    "Pacific Standard Time": "America/Los_Angeles",
    "US Mountain Standard Time": "America/Phoenix",
    "Mountain Standard Time (Mexico)": "America/Mazatlan",
    "Mountain Standard Time": "America/Denver",
    "Yukon Standard Time": "America/Whitehorse",
    "Central America Standard Time": "America/Guatemala",
    "Central Standard Time": "America/Chicago",
    "Easter Island Standard Time": "Pacific/Easter",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Canada Central Standard Time": "America/Regina",
    "SA Pacific Standard Time": "America/Bogota",
    "Eastern Standard Time (Mexico)": "America/Cancun",
    "Eastern Standard Time": "America/New_York",
    "Haiti Standard Time": "America/Port-au-Prince",
    "Cuba Standard Time": "America/Havana",
    "US Eastern Standard Time": "America/Indianapolis",
    "Turks And Caicos Standard Time": "America/Grand_Turk",
    "Paraguay Standard Time": "America/Asuncion",
    "Atlantic Standard Time": "America/Halifax",
    "Venezuela Standard Time": "America/Caracas",
    "Central Brazilian Standard Time": "America/Cuiaba",
    "SA Western Standard Time": "America/La_Paz",
    "Pacific SA Standard Time": "America/Santiago",
    "Newfoundland Standard Time": "America/St_Johns",
    "Tocantins Standard Time": "America/Araguaina",
    "E. South America Standard Time": "America/Sao_Paulo",
    "SA Eastern Standard Time": "America/Cayenne",
    "Argentina Standard Time": "America/Buenos_Aires",
    "Greenland Standard Time": "America/Godthab",
    "Montevideo Standard Time": "America/Montevideo",
    "Magallanes Standard Time": "America/Punta_Arenas",
    "Saint Pierre Standard Time": "America/Miquelon",
    "Bahia Standard Time": "America/Bahia",
    "UTC-02": "Etc/GMT+2",
    "Azores Standard Time": "Atlantic/Azores",
    "Cape Verde Standard Time": "Atlantic/Cape_Verde",
    "UTC": "Etc/UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "Sao Tome Standard Time": "Africa/Sao_Tome",
    "Morocco Standard Time": "Africa/Casablanca",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris",
    "Central European Standard Time": "Europe/Warsaw",
    "W. Central Africa Standard Time": "Africa/Lagos",
    "GTB Standard Time": "Europe/Bucharest",
    "Middle East Standard Time": "Asia/Beirut",
    "Egypt Standard Time": "Africa/Cairo",
    "E. Europe Standard Time": "Europe/Chisinau",
    "West Bank Standard Time": "Asia/Hebron",
    "South Africa Standard Time": "Africa/Johannesburg",
    "FLE Standard Time": "Europe/Kiev",
    "Israel Standard Time": "Asia/Jerusalem",
    "South Sudan Standard Time": "Africa/Juba",
    "Kaliningrad Standard Time": "Europe/Kaliningrad",
    "Sudan Standard Time": "Africa/Khartoum",
    "Libya Standard Time": "Africa/Tripoli",
    "Namibia Standard Time": "Africa/Windhoek",
    "Jordan Standard Time": "Asia/Amman",
    "Arabic Standard Time": "Asia/Baghdad",
    "Syria Standard Time": "Asia/Damascus",
    "Turkey Standard Time": "Europe/Istanbul",
    "Arab Standard Time": "Asia/Riyadh",
    "Belarus Standard Time": "Europe/Minsk",
    "Russian Standard Time": "Europe/Moscow",
    "E. Africa Standard Time": "Africa/Nairobi",
    "Volgograd Standard Time": "Europe/Volgograd",
    "Iran Standard Time": "Asia/Tehran",
    "Arabian Standard Time": "Asia/Dubai",
    "Astrakhan Standard Time": "Europe/Astrakhan",
    "Azerbaijan Standard Time": "Asia/Baku",
    "Russia Time Zone 3": "Europe/Samara",
    "Mauritius Standard Time": "Indian/Mauritius",
    "Saratov Standard Time": "Europe/Saratov",
    "Georgian Standard Time": "Asia/Tbilisi",
    "Caucasus Standard Time": "Asia/Yerevan",
    "Afghanistan Standard Time": "Asia/Kabul",
    "West Asia Standard Time": "Asia/Tashkent",
    "Qyzylorda Standard Time": "Asia/Qyzylorda",
    "Ekaterinburg Standard Time": "Asia/Yekaterinburg",
    "Pakistan Standard Time": "Asia/Karachi",
    "India Standard Time": "Asia/Calcutta",
    "Sri Lanka Standard Time": "Asia/Colombo",
    "Nepal Standard Time": "Asia/Katmandu",
    "Central Asia Standard Time": "Asia/Bishkek",
    "Bangladesh Standard Time": "Asia/Dhaka",
    "Omsk Standard Time": "Asia/Omsk",
    "Myanmar Standard Time": "Asia/Rangoon",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Altai Standard Time": "Asia/Barnaul",
    "W. Mongolia Standard Time": "Asia/Hovd",
    "North Asia Standard Time": "Asia/Krasnoyarsk",
    "N. Central Asia Standard Time": "Asia/Novosibirsk",
    "Tomsk Standard Time": "Asia/Tomsk",
    "China Standard Time": "Asia/Shanghai",
    "North Asia East Standard Time": "Asia/Irkutsk",
    "Singapore Standard Time": "Asia/Singapore",
    "W. Australia Standard Time": "Australia/Perth",
    "Taipei Standard Time": "Asia/Taipei",
    "Ulaanbaatar Standard Time": "Asia/Ulaanbaatar",
    "Aus Central W. Standard Time": "Australia/Eucla",
    "Transbaikal Standard Time": "Asia/Chita",
    "Tokyo Standard Time": "Asia/Tokyo",
    "North Korea Standard Time": "Asia/Pyongyang",
    "Korea Standard Time": "Asia/Seoul",
    "Yakutsk Standard Time": "Asia/Yakutsk",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "AUS Central Standard Time": "Australia/Darwin",
    "E. Australia Standard Time": "Australia/Brisbane",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "West Pacific Standard Time": "Pacific/Port_Moresby",
    "Tasmania Standard Time": "Australia/Hobart",
    "Vladivostok Standard Time": "Asia/Vladivostok",
    "Lord Howe Standard Time": "Australia/Lord_Howe",
    "Bougainville Standard Time": "Pacific/Bougainville",
    "Russia Time Zone 10": "Asia/Srednekolymsk",
    "Magadan Standard Time": "Asia/Magadan",
    "Norfolk Standard Time": "Pacific/Norfolk",
    "Sakhalin Standard Time": "Asia/Sakhalin",
    "Central Pacific Standard Time": "Pacific/Guadalcanal",
    "Russia Time Zone 11": "Asia/Kamchatka",
    "New Zealand Standard Time": "Pacific/Auckland",
    "UTC+12": "Etc/GMT-12",
    "Fiji Standard Time": "Pacific/Fiji",
    "Chatham Islands Standard Time": "Pacific/Chatham",
    "UTC+13": "Etc/GMT-13",
    "Tonga Standard Time": "Pacific/Tongatapu",
    "Samoa Standard Time": "Pacific/Apia",
    "Line Islands Standard Time": "Pacific/Kiritimati",
}

_FOLD = re.compile(r"\r?\n[ \t]")


def unfold(ics: str) -> str:
    """RFC 5545 §3.1 line unfolding. Outlook folds at 75 octets — a Meet URL, a UID or an
    ORGANIZER address arrives split, and every regex below would match half a value."""
    return _FOLD.sub("", ics.replace("\r\n", "\n"))


def decode_ics(raw: bytes) -> str:
    """Decode a text/calendar payload. Exchange connectors have been observed emitting UTF-16LE
    with a BOM (Outlook's own writer is UTF-8); sniff the BOM before assuming UTF-8."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


def resolve_tzid(tzid: str):
    """TZID → tzinfo, or None when the name cannot be resolved.

    Returning None (rather than raising) is load-bearing: the mailbox poll advances its cursor
    only after a message is routed, so a `ZoneInfoNotFoundError` on ONE Outlook invite would
    wedge the whole inbox in a retry loop. An unresolvable zone degrades to a floating time."""
    name = (tzid or "").strip().strip('"').strip("'")
    if not name:
        return None
    from zoneinfo import ZoneInfo
    for candidate in (WINDOWS_TO_IANA.get(name), name):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except Exception:  # noqa: BLE001 — unknown zone is a data fact, never an exception here
            continue
    return None


def parse_ics(ics: str) -> dict | None:
    """VEVENT → the invite facts, or None when this is not an admissible invite.

    Never raises: every external-format defect is a None or a degraded field."""
    if not ics:
        return None
    ics = unfold(ics)
    if "BEGIN:VEVENT" not in ics:
        return None                       # no event block — never fall back to scanning VTIMEZONE
    ve = ics.split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0]
    url = re.search(r"https://meet\.google\.com/[a-z-]+", ve) or \
        re.search(r"https://meet\.google\.com/[a-z-]+", ics)
    if not url:
        return None
    org = re.search(r"ORGANIZER[^:]*:(?:mailto:)?([^\s]+)", ve, re.I)
    dt = re.search(r'DTSTART(?:;TZID=("[^"]+"|[^:;]+))?[^:]*:(\d{8}T\d{6})(Z?)', ve)
    uid = re.search(r"^UID:(.+)$", ve, re.M)
    summ = re.search(r"^SUMMARY:(.+)$", ve, re.M)
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
            tz = resolve_tzid(dt.group(1))
            start = datetime(*t[:6], tzinfo=tz).timestamp() if tz else time.mktime(t)
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
            "group": group}
