"""OUTLOOK / EXCHANGE ICS — the reading rules Microsoft's calendar needs and Google's does not.

`mailbox.parse_ics` is the one parser. This module is everything that parser has to know about
Microsoft specifically, kept apart from it deliberately: the Microsoft rules are a body of
external-format knowledge that grows on its own cadence, and the parser is a hot file several
branches touch at once.

Three of these came off PR Vexa-ai/vexa#1318 (Exchange mail edge) and three off Vexa-ai/vexa#1320
(the M365 rig, measured against a live Microsoft 365 tenant on 2026-08-24). Both PRs targeted an
abandoned base and were never merged anywhere; this is the substance, ported.

  1. **Folding.** Outlook folds at 75 octets, hard. A Teams join URL is ~200 characters, so it
     arrives split over three physical lines. Unfold FIRST or every regex matches half a value.
  2. **Windows timezone names.** Outlook writes `TZID:"W. Europe Standard Time"` — never an IANA
     zone, and OFTEN QUOTED. The Windows→IANA mapping itself lives in `mailbox._WINDOWS_ZONES` /
     `mailbox._zone`, which is its owner and is NOT duplicated here; what this module's port added
     to it is the QUOTING, which is Microsoft's and belongs to neither name: `_zone` was handed
     `"W. Europe Standard Time"` with the quotes still on it, missed the table, and fell back to
     UTC — an hour wrong, silently, for exactly the pilot's own zone.
  3. **Encoding.** Exchange connectors have been observed emitting UTF-16LE with a BOM.
  4. **`LOCATION` never carries the Teams join URL** — it is the literal string
     "Microsoft Teams Meeting". The single most likely wrong guess.
  5. **`DESCRIPTION` carries TWO different links and the FIRST one is the wrong one.** Microsoft
     writes the short form `teams.microsoft.com/meet/<digits>?p=<passcode>` on the "Join:" line
     and the canonical `/l/meetup-join/19%3ameeting_…%40thread.v2` further down after "System
     reference:". A first-match regex yields a DIFFERENT IDENTIFIER FOR THE SAME MEETING than the
     `X-` property does — silently, with no error anywhere.
  6. **`\\n` inside an ICS TEXT value is two characters**, a literal backslash and an n. A URL
     character class that permits `\\` runs straight past the end of the link into the next
     line's prose (`…?p=Hsp…\\nMeeting`).

Stdlib only, no I/O, no clock — every function here is pure and unit-tested offline.
"""
from __future__ import annotations

import re
import urllib.parse

_FOLD = re.compile(r"\r?\n[ \t]")


def unfold_ics(ics: str) -> str:
    """RFC 5545 §3.1 line unfolding — a CRLF followed by one space or tab continues the line.

    THE precondition of reading a Microsoft ICS. Exchange folds every line at 75 octets, and a
    Teams join URL is ~200 characters, so it arrives split across three physical lines. A regex
    run over the RAW text matches only the first fragment and yields a truncated, unjoinable URL.
    Unfold FIRST, always. `mailbox._unfold` is this function."""
    return _FOLD.sub("", ics or "")


def decode_ics(raw: bytes) -> str:
    """Decode a `text/calendar` payload. Outlook's own writer is UTF-8, but Exchange connectors
    have been observed emitting UTF-16LE with a BOM — sniff before assuming."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


# ── Teams evidence ───────────────────────────────────────────────────────────────────────────
#
# Property names below are VERBATIM from a live Exchange-composed `METHOD:REQUEST` invitation
# captured 2026-08-24 (Vexa-ai/vexa#1320). Order is the order a parser should prefer them: the
# `X-` properties are machine-written and unambiguous; the rest are human-facing text that also
# happens to contain a link.
TEAMS_URL_PROPERTIES = (
    "X-MICROSOFT-SKYPETEAMSMEETINGURL",
    "X-MICROSOFT-ONLINEMEETINGEXTERNALLINK",
    "X-MICROSOFT-ONLINEMEETINGCONFLINK",
    "LOCATION",
    "DESCRIPTION",
    "X-ALT-DESC",
)

# `\` is EXCLUDED from the character class on purpose — see rule 6 in the module docstring. So is
# `;`, which ends a parameter list, and `>` which closes an angle-bracketed URL.
_TEAMS_URL = re.compile(r"https://teams\.(?:microsoft|live)\.com/[^\s<>\"'>;\\]+", re.I)
_TEAMS_THREAD = re.compile(r"19:meeting_[^@%\s/]+@thread\.v2", re.I)
_TEAMS_SHORT = re.compile(r"/meet/([^/?#]+)", re.I)


def _vevent(ics: str) -> str:
    """The VEVENT block, unfolded — or "" when there is none.

    Scanning the whole file is how a parser ends up reading the wrong block; `mailbox.parse_ics`
    learned that with DTSTART and VTIMEZONE, and a VTIMEZONE has no join links either."""
    if not ics or "BEGIN:VEVENT" not in ics:
        return ""
    return unfold_ics(ics).split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0]


def _unescape(value: str) -> str:
    """ICS TEXT escaping, undone for the two characters that appear inside URLs."""
    return value.replace("\\,", ",").replace("\\;", ";")


def ics_teams_properties(ics: str) -> dict[str, str]:
    """ICS property name → the FIRST Teams join URL found in it. What Microsoft actually
    populated for this event — the input a calendar parser has to be written against."""
    found: dict[str, str] = {}
    for line in _vevent(ics).splitlines():
        name = line.split(":", 1)[0].split(";", 1)[0].strip().upper()
        if name not in TEAMS_URL_PROPERTIES:
            continue
        m = _TEAMS_URL.search(_unescape(line))
        if m and name not in found:
            found[name] = m.group(0).rstrip(">).,;")
    return found


def teams_native_id(url: str) -> tuple[str, str] | None:
    """(platform, native_meeting_id) for a Teams join URL — the pair `POST /bots` wants.

    A local read of the same rule as meeting-api's canonical parser
    (`core/meetings/services/meeting-api/src/meeting_api/collector/meeting_link.py`
    :func:`parse_meeting_url`), Teams branch only; flows must stay import-free of the services.
    If the two ever disagree, that file wins."""
    value = urllib.parse.unquote(url or "")
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    if "teams.microsoft.com" not in host and "teams.live.com" not in host:
        return None
    thread = _TEAMS_THREAD.search(value)
    if thread:
        return ("teams", thread.group(0))
    short = _TEAMS_SHORT.search(urllib.parse.urlparse(url).path)
    if short:
        return ("teams", short.group(1))
    return None


def ics_teams_evidence(ics: str) -> dict:
    """Everything an Exchange-composed VEVENT says about its Teams meeting.

    Preference order, and it is the whole point of this function:
    `X-MICROSOFT-SKYPETEAMSPROPERTIES.cid` (the thread id ALREADY DECODED — no URL parsing at
    all) → `X-MICROSOFT-SKYPETEAMSMEETINGURL` → DESCRIPTION's **last** `meetup-join` match.
    Never LOCATION.

    Returns {} for anything that is not a VEVENT — absent is reported as absent, never guessed.
    """
    ve = _vevent(ics)
    if not ve:
        return {}
    urls: dict[str, list[str]] = {}
    thread_id = None
    provider = None
    for line in ve.splitlines():
        name = line.split(":", 1)[0].split(";", 1)[0].strip().upper()
        value = line.split(":", 1)[1] if ":" in line else ""
        unescaped = _unescape(value)
        if name == "X-MICROSOFT-SKYPETEAMSPROPERTIES":
            m = re.search(r'"cid"\s*:\s*"([^"]+)"', unescaped)
            if m:
                thread_id = m.group(1)
        elif name == "X-MICROSOFT-ONLINEMEETINGINFORMATION":
            m = re.search(r'"OnlineMeetingProvider"\s*:\s*(\d+)', unescaped)
            if m:
                provider = int(m.group(1))
        if name in TEAMS_URL_PROPERTIES:
            hits = [u.rstrip(">).,;") for u in _TEAMS_URL.findall(unescaped)]
            if hits:
                urls[name] = hits
    if not thread_id:
        for prop in ("X-MICROSOFT-SKYPETEAMSMEETINGURL", "DESCRIPTION"):
            for url in reversed(urls.get(prop, [])):
                parsed = teams_native_id(url)
                if parsed and parsed[1].startswith("19:meeting_"):
                    thread_id = parsed[1]
                    break
            if thread_id:
                break
    return {"thread_id": thread_id,
            "online_meeting_provider": provider,   # 3 == teamsForBusiness
            "join_urls": urls}


def teams_join_url(ics: str) -> str | None:
    """The ONE Teams join URL to admit for this invite, or None.

    `X-MICROSOFT-SKYPETEAMSMEETINGURL` first — one value, machine-written, canonical. Then
    DESCRIPTION's **LAST** match, because its first is the short-form id (rule 5). LOCATION is
    never consulted: it holds the literal string "Microsoft Teams Meeting" (rule 4).

    This is what `mailbox._meeting_url` asks before its own patterns, and it exists because those
    patterns scan the event top-to-bottom: on a real Exchange invite that means LOCATION's prose,
    then DESCRIPTION's short form — the wrong identifier, admitted with no error."""
    ev = ics_teams_evidence(ics)
    if not ev:
        return None
    urls = ev["join_urls"]
    canonical = urls.get("X-MICROSOFT-SKYPETEAMSMEETINGURL")
    if canonical:
        return canonical[0]
    for prop in ("X-MICROSOFT-ONLINEMEETINGEXTERNALLINK", "X-MICROSOFT-ONLINEMEETINGCONFLINK"):
        if urls.get(prop):
            return urls[prop][0]
    for prop in ("DESCRIPTION", "X-ALT-DESC"):
        hits = urls.get(prop) or []
        for url in reversed(hits):
            parsed = teams_native_id(url)
            if parsed and parsed[1].startswith("19:meeting_"):
                return url
        if hits:
            return hits[-1]
    return None


def decode_header_text(value: str) -> str:
    """RFC 2047 header decoding.

    Exchange encodes the subject whenever it holds a non-ASCII character, and an em dash is
    enough: `=?Windows-1252?Q?Vexa_rig_=97_ICS_probe?=`. A substring match on such a subject
    silently never fires — it cost the rig one four-minute polling window that reported FAIL
    while the mail sat in the inbox.

    NOT wired into the IMAP path: `inbox.from_rfc822` hands `Subject` through raw and that
    behaviour is deliberately unchanged here (see the receipt / the follow-up issue)."""
    from email.header import decode_header
    out = []
    for chunk, enc in decode_header(value or ""):
        out.append(chunk.decode(enc or "utf-8", errors="replace")
                   if isinstance(chunk, bytes) else chunk)
    return "".join(out)
