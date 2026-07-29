- **Operators can route terminal meeting facts to one signed, boot-frozen system callback (#992).**
  The optional destination accepts only `meeting.completed` and `bot.failed`, has its own
  retry/dead-letter lane, and may explicitly target an in-cluster HTTP service without weakening
  the SSRF guard on customer-configured webhooks. See
  [Configuration](/configuration#operator-terminal-callback).
