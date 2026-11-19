- **Flows mailbox: an IMAP server other than Gmail needs the authserv-id it trusts.** Set
  `VEXA_FLOWS_MAIL_AUTHSERV_ID` (Helm `flows.mail.authservId`) to the authserv-id your receiving
  server or mail gateway writes in `Authentication-Results`, for example on an on-premises
  Exchange. Without it the mailbox does not start. Only a verdict under that id counts; a mail with
  none is treated as unauthenticated, so its invites are quarantined. The id is public, so it
  protects only when that server writes its own `Authentication-Results` on every message it accepts
  and removes any arriving header that already carries its id; otherwise a sender can forge one. The Gmail preset and the
  Microsoft Graph inbox need nothing. See [Flows operations](/flows/operations).
