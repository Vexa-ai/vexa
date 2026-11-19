- **A mail reply's reaction now holds a bounded excerpt, never the message.** A `mail.reply`
  reaction stores seven refs only: the thread (`uid`, `session`), the sender, a one-line subject and
  Message-ID, and the sender's own words with the quoted thread dropped (`>` lines, the
  `On … wrote:` line, Outlook's `-----Original Message-----` history). A text attachment is no
  longer read as the body of an HTML-only mail. `VEXA_FLOWS_MAIL_BODY_MAX` now has a ceiling of
  16000 characters (a higher value is read as 16000), and the excerpt is also held to 32 KiB
  stored, so one reaction's refs are at most 36 KiB whatever the mail. The mailbox poller and the
  `mailbox` sensor kind build these refs the same way. See
  [Operating workflows](/flows/operations#what-a-mail-replys-reaction-holds).
