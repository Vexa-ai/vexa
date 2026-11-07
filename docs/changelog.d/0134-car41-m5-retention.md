- **Captured facts and the CRM audit now expire.** flows-worker removes finished reactions (their
  refs, inbound mail bodies included, scratch, receipts and signals), quarantined mail and old mail
  turns 90 days after the run finished, and clears the payload of spent keyed schedules
  (`VEXA_FLOWS_FACT_RETENTION_DAYS`, Helm `flows.factRetentionDays`, `0` keeps everything). A run
  still waiting is never removed. `crm_audit` keeps a row for 365 days (`CRM_AUDIT_RETENTION_DAYS`,
  Helm `crm.auditRetentionDays`, `0` keeps it for ever); crm-api removes older rows hourly through
  `crm_purge_audit()`, and inside the period the audit stays append-only. The CRM schema version is
  now 10, so `crm-init` must run before the new crm-api starts. See
  [Operating workflows](/flows/operations#how-long-captured-facts-are-kept) and [CRM](/minutes/crm).
