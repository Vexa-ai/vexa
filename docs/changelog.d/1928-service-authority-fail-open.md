- **Service authority outages no longer stop running bots (#1928, #1923).** An unavailable authority at
  a minute boundary is recorded as debt on the meeting (`service_authority.debt_boundaries`, `debt_open`)
  instead of stopping the bot; on recovery each debt minute is replayed exactly once, also when the
  meeting has stopped or ended by then. Admission is unchanged (`503 service_authority_unavailable`). See
  [Configuration](/configuration#optional-service-authority).
