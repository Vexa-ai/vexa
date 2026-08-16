# fixtures — invitations, as the wire carries them

Two shapes, because the mailroom has to be right about two different things.

- **`ics/`** — bare `.ics` bodies. The tests wrap them in the minimal message a calendar client
  sends (`conftest.envelope`), which is also how an out-of-repo corpus is replayed. `ics/oracle/`
  is the 22-fixture Stage-0 corpus; the files beside it cover cases the oracle does not.
- **`eml/`** — complete RFC-822 messages, for the MIME shapes an `.ics` cannot express: Google's
  `multipart/mixed` → `multipart/alternative` with `text/calendar; method=REQUEST`, Exchange
  shipping the invitation ONLY as a base64 `application/octet-stream` named `invite.ics`, and an
  ordinary email (the mailbox is public, so most of what lands in it is not an invitation).

All addresses are `example.com` (RFC 2606) or the dev workspace address `mk-dev@dev.vexa.ai`;
every meeting URL is fake but shape-valid.
