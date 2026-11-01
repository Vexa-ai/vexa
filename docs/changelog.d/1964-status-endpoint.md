- **Public `GET /status` and an `X-Vexa-Maintenance` header during maintenance (#1964).** The
  gateway now answers `GET /status` without a key — `operational`, `maintenance` or `degraded`, per
  component — and, while an update or incident is announced, stamps `X-Vexa-Maintenance: <id>` on
  every HTTP reply the gateway builds (not a bare unhandled `500`). `/status` is excluded from the per-IP edge guard like `/health`. See
  [Status](/api/status).
