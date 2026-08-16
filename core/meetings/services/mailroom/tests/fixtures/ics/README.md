# fixtures/ics — local `.ics` corpus (the cases the oracle does not cover)

Hand-written bodies for the shapes `ics/oracle/` leaves out, asserted by `tests/test_corpus.py`:

| file | asserts |
|---|---|
| `google-request-meet.ics` | the baseline: `X-GOOGLE-CONFERENCE`, `SEQUENCE:0`, one-off |
| `google-request-description-link.ics` | the link recovered from `DESCRIPTION` alone |
| `outlook-request-teams.ics` | Windows `TZID` + `X-MICROSOFT-SKYPETEAMSMEETINGURL` |
| `outlook-request-teams-short.ics` | the `teams.live.com/meet/<id>` short link |
| `zoom-request.ics` | Zoom (`LOCATION`) — the platform the oracle omits |
| `google-recurring-weekly.ics` | `RRULE` → the next occurrence, series binding |
| `google-recurring-update-seq2.ics` | the same UID at `SEQUENCE:2`, moved |
| `google-recurring-exdate.ics` | `EXDATE` skips an occurrence |
| `google-cancel.ics` | `METHOD:CANCEL` |
| `outlook-cancel-status-only.ics` | `STATUS:CANCELLED` with no `METHOD` |
| `plus-tagged-address.ics` | `mk-dev+notes@` resolves as `mk-dev@` |
| `negative-no-link.ics` | an in-person meeting → notice, no binding |
| `negative-no-uid.ics` | a `VEVENT` with no `UID` |
| `negative-malformed.ics` | garbage that must not crash the poller |
| `negative-reply-rsvp.ics` | `METHOD:REPLY` — somebody else's RSVP changes nothing |
| `negative-not-invited.ics` | delivered to us, but the `ATTENDEE` list never names us |

Times are absolute (2026-08-18 … 2026-08-26) against a pinned clock, so every expectation is
deterministic.
