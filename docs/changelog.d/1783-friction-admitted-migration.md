- **Friction reports on a deployment that ran an early friction-dedup build (#1783).** A
  `friction_occurrence` table created before its `admitted` column existed now gains the column at
  boot (`flows.migrations`, idempotent); existing rows are kept and marked admitted. Before, every
  friction report on such a deployment failed with HTTP 500. Fresh installs are unchanged.
