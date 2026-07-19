- **Webhook deliveries now report every outcome (#815).** `WebhookSink.deliver` returns
  `delivered | suppressed | blocked | failed | queued`, and that outcome used to be discarded — so a
  webhook a subscriber never received (an event type outside their filter, an SSRF-refused target, an
  endpoint returning 4xx) was indistinguishable from one that arrived, and even successful deliveries
  logged nothing. Each delivery now emits one `webhook_delivery` logevent carrying the outcome, the
  event type, the target **host** (never the full URL — it can carry a token in its path or query),
  the HTTP status and the error, at `warning` for anything that did not arrive. "My webhooks stopped"
  is now a one-query answer instead of a silent failure. A compose-stack test asserts the outcome is
  reported end-to-end.
