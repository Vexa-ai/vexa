- **The edge guard's block logs no longer carry credentials.** Every request the gateway's
  per-IP guard rate-limits or blocks writes a log line with the request's headers and URL. Those
  lines now show `X-API-Key`, `Authorization`, `Cookie` and query credentials such as `api_key`
  as `[REDACTED]`; before, they were written in clear. The gateway requires guard-core 4.4 or later.
  The guard's own 429 now carries `Retry-After` set to its rate window (60s by default).
