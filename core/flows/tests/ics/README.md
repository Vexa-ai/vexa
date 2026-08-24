# ICS fixtures for the invite-intake parser

Five real-shaped invites. They exist because the intake used to carry one regex —
`https://meet\.google\.com/[a-z-]+` — and a bank's Outlook invite carries a **Microsoft Teams**
link, so the first real invite from a Teams shop parsed as "no meeting here" and was logged as
ignored.

## Provenance

| file | where it came from |
|---|---|
| `outlook-teams-deep-link.ics` | byte-copy of `fixtures/mailroom/ics/outlook-create-single.ics` in [`DmitriyG228/biz`](https://github.com/DmitriyG228/biz) — the RFC-5545-validated mailroom corpus generated 2026-08-17 (`_generate.py` owns the folding/CRLF/escaping; `_validate.py` asserts conformance against `icalendar` 6.3.2). **Only change: `DTSTART`/`DTEND` shifted 2026 → 2030** so the invite is in the future, because `parse_ics` refuses a past start. |
| `outlook-teams-description-only.ics` | same corpus, `outlook-create-single-bot-only.ics`, same date shift. The Teams URL exists **only** in the folded, ICS-escaped `DESCRIPTION`; `LOCATION` is the useless literal `Microsoft Teams Meeting` and there is no `X-MICROSOFT-SKYPETEAMSMEETINGURL`. |
| `gcal-meet.ics` | same corpus, `gcal-create-single.ics`, same date shift. The Google Meet regression case: `X-GOOGLE-CONFERENCE` + `DESCRIPTION`, empty `LOCATION`. |
| `outlook-teams-short-link.ics` | **derived** from `outlook-teams-deep-link.ics` in this repo: the deep link swapped for Microsoft's newer short meeting link `teams.microsoft.com/meet/<id>?p=<passcode>`, in both `X-MICROSOFT-SKYPETEAMSMEETINGURL` and `DESCRIPTION`. The short-link shape is the one the product's own parser recognises (`_TEAMS_SHORT` in `core/meetings/services/meeting-api/src/meeting_api/collector/meeting_link.py`). Folding, CRLF and TEXT escaping follow the corpus discipline. |
| `outlook-zoom.ics` | **derived** the same way: the Teams properties removed and a Zoom join URL put in `LOCATION` + `DESCRIPTION`. This is the invite that must fail **typed** — recognised as `zoom`, refused with an explanation, never dispatched at. |

Every address is `example.com` (RFC 2606); every meeting URL, thread id and Exchange Global
Object ID is synthetic but shape-valid.

## What each one is for

| file | asserts |
|---|---|
| `outlook-teams-deep-link.ics` | the bank case: Teams thread id out of `X-MICROSOFT-SKYPETEAMSMEETINGURL`, which outranks the body; `X-MICROSOFT-SCHEDULINGSERVICEUPDATEURL` (a management endpoint carrying the same id, unencoded) is **not** mistaken for the join link |
| `outlook-teams-description-only.ics` | the id survives RFC-5545 folding and `\n` / `\,` escaping in the body when no X- property exists |
| `outlook-teams-short-link.ics` | `/meet/<id>` + the `?p=` passcode, carried separately from the id |
| `gcal-meet.ics` | regression: every Meet invite that parsed before the change still parses |
| `outlook-zoom.ics` | a recognised-but-unsupported platform is a **named refusal**, not silence |
