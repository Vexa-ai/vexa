- **The bot mailbox reads any IMAP server, not only Gmail.** Invite-by-email now works with an
  on-premises Exchange or any other IMAP server: `VEXA_MAIL_IMAP_HOST`, `_PORT`, `_TLS` (`tls`,
  `starttls`, or `none` for a lab), `_USER` (a login name other than the address), `_FOLDER` and
  `_CA_FILE` (an internal CA). Every default is the Gmail preset, so an existing install behaves as
  before. Flows' relay gains `VEXA_MAIL_SMTP_TLS` (`starttls` can now be required) and
  `VEXA_MAIL_SMTP_CA_FILE`. Mail-server failures are typed (`mail:auth`, `mail:tls`,
  `mail:connect`, `mail:config`): the mailbox no longer restart-loops on a wrong password, it turns
  NotReady with the reason and recovers by itself, and a failed send names the cause on the
  reaction. Helm: `flows.mail.imap`, `.smtp`, `.ca`, `.existingSecret` and `.enabled`; the mailbox
  Deployment renders only when it has an address or is enabled, and flows now sends through
  `terminal.mail`'s relay when `flows.mail.smtp.host` is empty. See
  [Connect the bot mailbox](/flows/mailbox).
