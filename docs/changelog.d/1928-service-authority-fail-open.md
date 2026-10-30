- **Service authority outages no longer stop running bots (#1928, #1923).** With an optional
  service authority configured, an authority that is unavailable at an active-service minute boundary
  used to stop a bot that was already running. That bot now keeps running. The minute is recorded as
  debt on the meeting (`service_authority.debt_boundaries`, `debt_open`), meeting-api logs
  `service_authority_fail_open` once per minute, and when the authority answers again the minutes of a
  still-running meeting are replayed once, in order (`debt_settled_count`). Debt of a meeting that
  stops or ends before the authority recovers stays open (`debt_open: true`) for now. Admission is
  unchanged: a new bot still gets `503 service_authority_unavailable` while the authority is down. See
  [Configuration](/configuration#optional-service-authority).
