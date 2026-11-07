# Exchange / Outlook ICS fixtures

Invite bodies in the shapes Microsoft emits, used by `tests/test_exchange_ics.py`. Each file's
header comment says what it is based on. None of these came from a live tenant — they are
constructed from documented Outlook/Exchange output, so they prove the PARSER, not the
connection. Live-tenant validation is still owed (no M365 credential exists in the vault). The one
thing that DID come off a live tenant is inline in `tests/test_m365_ics.py` — a real Microsoft 365
invitation captured 2026-08-24, folded exactly as Exchange folded it.

Ported from PR Vexa-ai/vexa#1318, which targeted an abandoned base and merged nowhere.

| file | what it exercises |
|---|---|
| `outlook-w-europe.ics` | `TZID:"W. Europe Standard Time"` (quoted Windows zone) + a Windows VTIMEZONE block whose STANDARD/DAYLIGHT rules are anchored in 1601/1970 |
| `outlook-folded.ics` | RFC 5545 §3.1 folding at 75 octets splitting the Meet URL, the UID and the ORGANIZER across lines |
| `outlook-pacific.ics` | `Pacific Standard Time` unquoted — a second zone, so the mapping is not a one-entry coincidence |
| `exchange-unknown-tz.ics` | `TZID:Customized Time Zone` — an unmappable name in Microsoft's QUOTED spelling, which must fall back to UTC and never raise. The unquoted case belongs to `tests/test_ics_exchange.py`, which owns the Windows -> IANA table |
| `outlook-utf16le.ics.b64` | base64 of a UTF-16LE-with-BOM encoded invite (observed on some Exchange connectors) |
