- **Workflows: three new services and ten Postgres tables (#1456).** `flows-api`, `flows-worker` and
  the optional `flows-mailbox` (one image, three commands) run durable product workflows — a fact
  from the outside world becomes one reaction row, a worker runs one step and records a receipt.
  They share the stack's Postgres and add ten tables of their own; no message broker, no scheduler.
  `flows-worker` is not behind a profile: a deployment that admits facts and never advances them is
  the failure this shape exists to avoid. See [Workflows](/flows/overview) and
  [Operating workflows](/flows/operations).
