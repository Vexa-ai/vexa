- **Service authority outages no longer stop running bots (#1928, #1923).** An unavailable authority at
  a minute boundary is recorded as debt on the meeting (`service_authority.debt_boundaries`, `debt_open`)
  instead of stopping the bot; each debt minute of a running meeting is replayed once on recovery.
  Admission is unchanged (`503 service_authority_unavailable`). See
  [Configuration](/configuration#optional-service-authority).
