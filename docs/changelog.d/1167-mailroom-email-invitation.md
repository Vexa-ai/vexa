- **Meetings can enter by email invitation, no calendar integration (#1167).** New `mailroom`
  service (dev, compose profile `mailroom`): a workspace has an email address, users invite it like
  any other attendee, and the `.ics` arriving in *our* mailbox becomes a planned meeting — the
  invited address is the workspace, so nothing needs read access to anyone's calendar. Recurring
  invitations bind the series and roll to the next occurrence; updates re-schedule the same meeting
  and cancellations stop attendance (`SEQUENCE`-keyed, so a re-delivered or out-of-order copy
  changes nothing). Anything it cannot resolve — a malformed invitation, no joinable link, a
  start time with no timezone, an address it does not serve — produces no meeting and a recorded
  notice rather than a guess. Consumes the public `POST`/`PATCH`/`DELETE /meetings` routes only;
  the inbound mailbox sits behind a port, so today's Mailpit is replaceable by IMAP or inbound SMTP.
