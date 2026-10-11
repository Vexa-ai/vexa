# flows_integrations

The edge: processes that turn the outside world into FACTS. `mailbox.py` — the real inbox:
ICS → invite.received; thread-matched replies → mail.reply; durable cursor (mail_cursor row)
so restarts resume, never re-admit.

`inbox.py` is the source seam underneath it: `VEXA_MAIL_INBOX=imap` (default) polls any IMAP
server — `VEXA_MAIL_IMAP_HOST`/`_PORT`/`_TLS`/`_USER`/`_FOLDER`/`_CA_FILE`, Gmail being the preset
every one of them defaults to (the wire and its typed `MailTransportError` are
`flows_steps/mail_transport.py`), `=mailpit` polls the dev stack's mail double over REST
(`VEXA_MAILPIT_URL`, filtered on `VEXA_MAIL_ADDR`, no mail password needed). Both fetch the raw
RFC822 source and share one parse, so the two produce identical facts. Mailpit ids are random
rather than monotonic, so its position is a `Created` watermark (`mail_cursor.token`) plus a
seen-id set (`mail_seen`) — see the contracts at the top of `inbox.py`.

`=graph` is the third source: Microsoft 365 over the Graph API with client-credentials, for a
tenant that exposes neither IMAP nor SMTP AUTH. `graph_client.py` is the wire (token, listing,
attachments, draft-then-send); `graph_inbox.py` is that wire behind the same five-method seam,
and it adds NO schema — Graph's position is a `receivedDateTime` watermark in `mail_cursor.token`
with `mail_seen` beside it, the same machinery mailpit established, because Graph's timestamp is
second-granular and two invitations can share one. Outbound goes through `flows_steps/notify.py`'s
`graph` channel and the same `graph_client`. Four keys, all four or none
(`VEXA_GRAPH_TENANT_ID`/`_CLIENT_ID`/`_CLIENT_SECRET`/`_MAILBOX`, the `mailbox_graph` capability).
**No live tenant has ever answered it** — fixtures and a fake HTTP layer only.

An Exchange mailbox with IMAP ENABLED — on-premises or Exchange Online — does not need any of that:
it is the IMAP source with its own host, login name and CA bundle.

`mailbox_status.py` is the mailbox's health: the poll loop writes how its last poll went (ok, or the
typed fault) to a file in its temp directory, and `python -m flows_integrations.mailbox_status` is
the readiness probe that reads it. Operator page, How it works and Why it complies:
`docs/docs/flows/mailbox.mdx`.

`outlook.py` is what the ICS parser has to know about Microsoft specifically, and only that:
RFC 5545 unfolding (Outlook folds at 75 octets and splits a Teams URL over three lines), UTF-16
BOM sniffing, and the Teams join-URL precedence — `X-MICROSOFT-SKYPETEAMSPROPERTIES.cid`, then
`X-MICROSOFT-SKYPETEAMSMEETINGURL`, then DESCRIPTION's LAST match, never `LOCATION`. The
Windows→IANA timezone table is NOT here: it belongs to `mailbox._zone`.
