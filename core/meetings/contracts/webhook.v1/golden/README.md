# webhook.v1 goldens

Wire-shape fixtures, one per `$def`/case. Filename `<Shape>.<case>.json`; the prefix names the
`$def` the file must conform to (validated by `../validate.mjs`, run by `gate:schema`).

- `Envelope.meeting-completed.json` — a `meeting.completed` delivery (per-client hook, terminal success).
- `Envelope.bot-failed.json` — a `bot.failed` delivery carrying the `status_change` block (terminal failure).
- `SignatureHeaders.signed.json` — the signed headers a verifier recomputes (`sha256=<hmac(ts.payload)>`).
