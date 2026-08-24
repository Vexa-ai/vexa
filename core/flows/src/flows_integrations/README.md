# flows_integrations

The edge: processes that turn the outside world into FACTS. `mailbox.py` — the real inbox:
ICS → invite.received; thread-matched replies → mail.reply; durable cursor (mail_cursor row)
so restarts resume, never re-admit.

## The mail transport seam

Which mailbox technology the front door is stops here. `mail_transport.py` defines one interface
and selects the wiring from a single env var; `mailbox.py` (fetch) and `flows_steps/emailx.py`
(send) are transport-blind, and no engine, flow or step code knows the difference.

| `VEXA_MAIL_TRANSPORT` | wiring | for |
|---|---|---|
| `gmail` (default) | IMAP/SMTP, Google host defaults | today's `info@vexa.ai` |
| `imap` | IMAP/SMTP, your hosts | Exchange/M365 **with IMAP enabled**, on-prem Exchange, anything else |
| `graph` | Microsoft Graph, client-credentials | M365 with IMAP off — the bank posture |

`gmail` and `imap` are the same code path; `gmail` only supplies host defaults.

```
# generic IMAP (Exchange Online)
VEXA_MAIL_IMAP_HOST=outlook.office365.com  VEXA_MAIL_IMAP_PORT=993  VEXA_MAIL_IMAP_FOLDER=INBOX
VEXA_MAIL_SMTP_HOST=smtp.office365.com     VEXA_MAIL_SMTP_PORT=587  VEXA_MAIL_SMTP_STARTTLS=1

# Graph  (Azure app registration: APPLICATION permissions Mail.ReadWrite + Mail.Send, admin
#         consent, scoped to the one mailbox with an ApplicationAccessPolicy)
VEXA_GRAPH_TENANT_ID=…  VEXA_GRAPH_CLIENT_ID=…  VEXA_GRAPH_CLIENT_SECRET=…
VEXA_GRAPH_MAILBOX=vexa@customer.tld       VEXA_GRAPH_USE_DELTA=0
```

Four contracts every transport keeps — a variant that breaks one is a regression, not a variant:
**C1** durable cursor, anchored at the tail on first boot (never replays history) · **C2** the
real `Message-ID` comes back from every send so `mail_thread` can route the reply by THREAD ·
**C3** ICS attachments are read and decoded · **C4** nothing sleeps or polls inside a fetch.

**Cursor column.** Graph's position is a delta link or an ISO timestamp, so `mail_cursor` grew a
nullable `token TEXT`. `schema.sql` is CREATE-IF-NOT-EXISTS with no migration runner, so on a
database that predates this change run once:
`ALTER TABLE mail_cursor ADD COLUMN token TEXT;` — IMAP keeps working without it; Graph refuses
to start rather than silently rewinding the mailbox.

## ICS

`ics.py` parses both Google and Outlook/Exchange shapes: RFC 5545 line unfolding first (Outlook
folds at 75 octets, splitting Meet URLs and addresses), Windows→IANA timezone mapping (CLDR
`windowsZones` world-default table, vendored in-source), UTF-16 BOM sniffing, and an
unresolvable zone degrades to a floating time rather than raising — a raise here would wedge the
cursor, since the poller only advances after a message is routed.
