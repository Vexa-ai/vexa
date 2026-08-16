# fixtures/eml — complete messages, for the MIME shapes an `.ics` cannot express

- **`google-invitation.eml`** — Google Calendar's real envelope: `multipart/mixed` wrapping a
  `multipart/alternative` (plain + html + `text/calendar; method=REQUEST`) plus an
  `application/ics` attachment carrying the same event.
- **`outlook-attachment-only.eml`** — the invitation ONLY as a base64
  `application/octet-stream; name="invite.ics"` (a relay transcoded the body). The parser must
  still find it.
- **`negative-plain-email.eml`** — an ordinary email. The workspace mailbox is a public address;
  most of what arrives is not an invitation, and none of it may produce a group effect.
