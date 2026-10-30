- **Service authority outages no longer stop running bots (#1928, #1923).** With an optional
  service authority configured, an authority that is unavailable at an active-service minute boundary
  used to stop a bot that was already running. That bot now keeps running. The minute is recorded as
  debt on the meeting, meeting-api logs `service_authority_fail_open` once per minute, and when the
  authority answers again each debt minute is replayed once, in order, including the remaining
  minutes of a meeting that was stopped by a replayed deny or ended during the outage. The meeting
  record shows `service_authority.debt_open` and `debt_settled_count`. Admission is unchanged: a new
  bot still gets `503 service_authority_unavailable` while the authority is down. See
  [Configuration](/configuration#optional-service-authority).
