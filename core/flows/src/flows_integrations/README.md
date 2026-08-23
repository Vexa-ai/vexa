# flows_integrations

The edge: processes that turn the outside world into FACTS. `mailbox.py` — the real inbox:
ICS → invite.received; thread-matched replies → mail.reply; durable IMAP cursor (mail_cursor row)
so restarts resume, never re-admit.
