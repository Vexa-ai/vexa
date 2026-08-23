# post_meeting

One concern: after the meeting worker has committed its meeting artifact, read the committed
summary and deliver an idempotent notification containing the Minutes chat door. The core service
depends only on `ArtifactReader` and `EmailSink`; filesystem and dev-SMTP implementations are
adapters. It may depend on Python's standard library and the surrounding agent worker package. It
does not parse transcripts, dispatch agents, or infer recipients.

`DevSmtpEmailSink` suppresses duplicate callbacks within one worker process and emits the stable
commit-derived key as `X-Vexa-Idempotency-Key`. Cross-process exactly-once delivery is deliberately
left to a future durable production `EmailSink`; the Mailpit adapter is only a development double.

Public surface: `post_meeting.__init__`.
