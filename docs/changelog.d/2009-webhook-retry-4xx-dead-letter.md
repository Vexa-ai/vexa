- **Webhooks: a retry refused with a 4xx is dead-lettered instead of vanishing (#2009).** When a
  webhook delivery has already failed once (5xx, 429 or timeout) and the receiver then answers the
  retry with a non-retryable status (a non-429 4xx such as `404`/`410`, or a 3xx), the envelope is
  moved to the dead-letter list with its status code and logged as `webhook_dead_lettered` (reason
  `non_retryable_status`). It is not retried again. Previously such a retry counted as delivered,
  and the envelope disappeared with no record. This applies to customer webhooks and the operator
  system callback alike. See [Webhooks](/webhooks).
