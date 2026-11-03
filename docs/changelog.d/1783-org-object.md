- **Orgs are recorded in identity (#1783).** Each install now has one org, `default`, which every
  account belongs to. Policy reads go through it and still resolve to `_global`. Its CRM
  organisation is taken from `CRM_ORG_ID`, and a billing-account field is reserved. Nothing you see
  changes. The schema step runs in place on a live database: no table is rewritten and readers never
  wait on it. Rollback is three statements, documented in `schema/MIGRATION-0008-orgs.md`.
