- **Refusals say what to do; outages say retry (#1784).** agent-api's `/invocations` and `/events`
  refuse a caller with `{status, reason, instruction}`, like every other refusal, and log it. An
  outage at Google or at a custom service now reaches the agent as `502`/`503` with a sentence that
  says reconnecting will not help, instead of a `409` that asked the person to reconnect; a true
  refusal (permission not granted, authorization rejected) stays `409`. The credential broker gains a
  readiness probe, `GET /ready`, that answers `503` while its credential store is down. A recording
  that cannot be made seekable is logged with its cause.
