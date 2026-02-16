# Errors, Retries, and Idempotency

Vexa is designed so you can safely retry **reads** and **cleanup** operations, and so production systems can recover from transient failures.

## Error Shape (Typical)

Most validation/auth errors are returned as JSON with a human-readable `detail` field.

Example (missing API key):

```json
{
  "detail": "Not authenticated"
}
```

## Retry Guidance

### Safe to retry (idempotent)

- `GET ...` (reads)
- `PUT ...` (settings updates)
- `DELETE /meetings/{platform}/{native_meeting_id}` (delete/anonymize)
- `DELETE /recordings/{recording_id}`

“Idempotent” means: calling the endpoint multiple times results in the same outcome, and repeated calls won’t cause additional side effects.

Example: if a meeting is already anonymized, deleting it again still returns success.

### Be careful retrying

- `POST /bots` can create *additional* bot runs if you retry blindly.

If you’re not sure whether a `POST /bots` succeeded:

1. Check the meeting exists (via `GET /transcripts/...` or `GET /meetings`)
2. Only retry if you can confirm no bot is currently running for that meeting

## Backoff Strategy

For transient failures, use exponential backoff with jitter:

- Retry on: `429`, `502`, `503`, `504`, network timeouts
- Don’t retry on: `400`, `401`, `403`, `404` (fix request/auth first)

## Webhooks

Webhook deliveries are best-effort and your endpoint may receive retries or repeated events.
Design webhook handlers to be:

- fast to ACK (2xx)
- idempotent (dedupe by event type + meeting/recording ids)

References:

- [Webhooks](webhooks.md)
- [Local webhook development](local-webhook-development.md)
