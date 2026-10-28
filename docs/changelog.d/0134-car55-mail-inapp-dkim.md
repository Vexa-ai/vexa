- **Flows mailbox: optional in-app DKIM and DMARC.** `VEXA_FLOWS_MAIL_AUTH_MODE` (Helm
  `flows.mail.authMode`) chooses whose verdict says a sender is who the `From:` says. `mta`, the
  default, is unchanged: the receiving server's `Authentication-Results`. `inapp` ignores that header
  and verifies the message's DKIM signature in flows, aligned with the `From:` domain under its DMARC
  record, so a forged header no longer passes and no authserv-id is needed. `both` requires the two.
  SPF is not checked in-app. The mailbox needs DNS TXT lookups in these modes, and the Graph inbox
  takes `mta` only. See [Flows operations](/flows/operations).
