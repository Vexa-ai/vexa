# fixtures/ics/oracle — the 22-fixture invitation corpus (the test oracle)

Generated for the Stage-0 invitation work and vendored here so the corpus test is hermetic.
`tests/test_oracle_corpus.py` is the executable form of the table below; this file explains what
each fixture is *for*. The workspace address in every fixture is `mk-dev@dev.vexa.ai`; all human
addresses are `example.com` (RFC 2606); every Meet/Teams URL and Exchange Global Object ID is
synthetic but shape-valid.

## Expected outcomes

| file | represents | expected |
|---|---|---|
| `gcal-create-single.ics` | `REQUEST` `SEQUENCE:0`, one-off, `Europe/Lisbon`, Meet URL in `X-GOOGLE-CONFERENCE` + `DESCRIPTION`, empty `LOCATION` | **bind** — 1 occurrence, 4 participants |
| `gcal-update-single.ics` | same UID, `SEQUENCE:1`, start moved | **update** — same row |
| `gcal-cancel-single.ics` | same UID, `METHOD:CANCEL` `SEQUENCE:2` | **cancel** |
| `gcal-create-recurring.ics` | `RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=6` | **bind** — series; occurrence 2 attended |
| `gcal-update-recurring.ics` | same UID, `SEQUENCE:1`, series moved | **update** — still one series |
| `gcal-cancel-recurring.ics` | same UID, `METHOD:CANCEL` | **cancel** — whole series |
| `outlook-create-single.ics` | `REQUEST`, Windows `TZID:W. Europe Standard Time`, Teams URL in `X-MICROSOFT-SKYPETEAMSMEETINGURL`, `VALARM` | **bind** |
| `outlook-update-single.ics` | same UID, `SEQUENCE:1` + `X-MICROSOFT-CDO-APPT-SEQUENCE:1` | **update** |
| `outlook-cancel-single.ics` | same UID, `METHOD:CANCEL`, **`SUMMARY` prefixed `Canceled: `** | **cancel** |
| `outlook-create-recurring.ics` | `RRULE …UNTIL=20260930T070000Z`, Teams URL also in `LOCATION` | **bind** — series |
| `outlook-update-recurring.ics` | same UID, `SEQUENCE:1`, series moved | **update** |
| `outlook-cancel-recurring.ics` | same UID, `METHOD:CANCEL` | **cancel** — whole series |
| `gcal-create-single-bot-only.ics` | the workspace is the ONLY attendee; URL only in a folded `DESCRIPTION` | **bind** — 1 participant |
| `gcal-create-single-bot-optional.ics` | the workspace is `ROLE=OPT-PARTICIPANT` | **bind** ⚑ |
| `outlook-create-single-bot-only.ics` | only attendee; URL only in `DESCRIPTION`; `LOCATION` is the useless literal | **bind** — 1 participant |
| `outlook-create-single-bot-optional.ics` | `ROLE=OPT-PARTICIPANT` | **bind** ⚑ |
| `neg-malformed-truncated-vevent.ics` | truncated mid-property, mid-fold — unparseable by design | **reject + notice** |
| `neg-no-meeting-url.ics` | valid invite, no conferencing URL anywhere (in-person) | **reject + notice** |
| `neg-bot-not-invited.ics` | reached the mailbox by forward/BCC; absent from `ATTENDEE` | **reject + notice** ⚑ |
| `neg-tzless-dtstart.ics` | `DTSTART:20260821T170000` — RFC-legal floating local time | **reject + notice** ⚑ |
| `neg-update-unknown-uid.ics` | `REQUEST` `SEQUENCE:1` for a UID never bound | **bind** ⚑ + a note that no prior binding existed |
| `neg-cancel-unknown-uid.ics` | `CANCEL` for a UID never bound | **ignore** ⚑ — no binding AND no notice |

⚑ = a product decision rather than a property of the bytes. Each is argued where it is
implemented (`service.py`, `invite.py`) and restated in `tests/test_oracle_corpus.py`'s docstring:
optional invitations bind · the `ATTENDEE` list is authoritative · floating time is refused, not
guessed · a `REQUEST` is authoritative whatever its `SEQUENCE` · an unknown `CANCEL` is silent.

## Replay ordering

Update and cancel fixtures are **stateful** — they only mean the above when their create has been
replayed first, into the same store. Four chains (`SEQUENCE` 0 → 1 → 2): gcal single, gcal
recurring, outlook single, outlook recurring. The remaining ten fixtures are independent.

Two more assertions come free from the ordering: replaying an update *after* its cancel must be
ignored (a lower `SEQUENCE` is an out-of-order delivery, not a resurrection), and replaying any
create twice must produce one binding.

## Traps a real parser has to survive

Windows `TZID` names anchored at `16010101` (not IANA-resolvable); a `SUMMARY` that changes on
cancel; a `LOCATION` that is sometimes the URL, sometimes the literal `Microsoft Teams Meeting`,
sometimes empty; conferencing URLs recoverable only from a folded `DESCRIPTION`; a non-ASCII `CN`
folded across a line boundary; `%`- and `\`-escaped TEXT.

## Conformance

`python3 _validate.py` (vendored beside the fixtures) re-asserts the corpus's own RFC 5545
discipline: CRLF throughout, 75-octet folding, valid UTF-8, one `VEVENT` per file, every file
parseable by `icalendar` — except `neg-malformed-truncated-vevent.ics`, which must NOT parse.

Every `DTSTART` is a fixed absolute date (2026-08-18 … 2026-08-24) so replays are deterministic.
To exercise the corpus against near-future dates instead, regenerate it with a whole-week shift
and point the tests at the result: `MAILROOM_ICS_CORPUS=<dir> uv run pytest -q tests/test_corpus.py`.
