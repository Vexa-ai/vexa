# Optional CRM record domain

CRM adds structured records and sourced narrative to Minutes. It does not own
meeting transcripts, mounted workspaces or workflow execution. Deployments may
omit this domain entirely.

The first contract is in `contracts/records.v1/`. Every operation is scoped to a
tenant and a verified caller. Imported source IDs are namespaced by source org;
they never replace permanent Vexa record IDs. Unsupported imported access rules
must quarantine affected records until their visibility can be represented.

Implementation and live validation are tracked in Vexa-ai/vexa issue 1762.

## Run and test

```sh
uv run --project core/crm pytest core/crm/tests -q
docker build -t vexa-crm:dev core/crm
```

Provision a **separate PostgreSQL database**, migration owner and runtime role.
Run `python -m crm.migrate` with `CRM_DATABASE_URL` set to the migration owner.
Grant the runtime role schema usage and SELECT on CRM tables; INSERT/UPDATE on
records, relationships, proposals and outbox; DELETE on relationships; INSERT on
revisions. Do not grant DDL, revision UPDATE/DELETE/TRUNCATE, policy writes or
import writes to the runtime role. The revision trigger also rejects mutation.
Run `python -m crm` with the runtime database URL, `ADMIN_API_URL` and
`INTERNAL_API_SECRET`. It serves port 8300. Existing core database tables are
neither migrated nor joined.

Configure `CRM_API_URL` on MCP and Minutes only when the module is deployed.
Minutes also accepts `CRM_DEFAULT_TENANT`; `/crm?tenant=...&record=...` opens an
individual record. `/api/crm` forwards the signed-in person's credential and
rejects cross-origin browser requests. No deployment-key fallback is used.
A configured CRM outage leaves other MCP tools available and CRM calls fail
explicitly; an unconfigured CRM contributes no tools or page.
`compose.example.yaml` illustrates the optional service wiring; network names and
identity URLs must match the deployment. It is not a database provisioner.

## Six operations

| MCP tool | HTTP | Purpose |
|---|---|---|
| `crm_describe` | POST `/describe` | Caller-visible objects and field definitions |
| `crm_search` | POST `/search` | Authorized, filtered and paged records |
| `crm_read` | POST `/read` | Fields, narrative, sources, permitted links and pending proposals |
| `crm_change` | POST `/change` | Create, update or propose with reason and evidence |
| `crm_history` | POST `/history` | Immutable revisions filtered by current permissions |
| `crm_review` | POST `/review` | Accept or reject; stale acceptance conflicts |

Every call requires `tenant_id` and a real identity credential. Caller-supplied
subject headers are ignored. Create requires a stable `idempotency_key`; reuse
it for the same create retry. Update/propose require `expected_revision` from a
read. The service validates typed fields against the object's JSON Schema.
Record, revision and outbox writes commit together. Review does not change the
record until acceptance. Permission checks also apply to search filters, linked
targets, history and proposal visibility; partial-field readers receive no
free-form narrative, history reasons or evidence that might reveal hidden fields.

The development control MCP's `crm_tools.py` adapter uses the same service and
verified caller. A human delegation may use the caller's ordinary CRM rights.
An autonomous workspace-scoped delegation needs an explicit `crm_tenants` grant;
workspace access alone never grants CRM access. Configuring that grant at an
operator's dispatch boundary remains necessary before autonomous CRM routines.

## Import

`PYTHONPATH=core/crm/src uv run --project core/crm python core/crm/scripts/import_export.py
<export-directory> --tenant <tenant> --actor <operator-subject> --user-map <json>`
loads describe JSON and CSV exports using the migration credential. The map is
source User ID to an **existing** Vexa subject ID; login credentials are never
copied from the source. Operator membership is provisioned separately in
`crm_access_policies`. Source IDs are namespaced by tenant and source org.

Preserved: custom object/field names, typed values, source IDs, relationships,
profile/permission-set field access, materialized shares, groups and role
hierarchy. Receipts retain input checksums. Reimport leaves identical records
alone and reports differing existing records for reconciliation; it does not
silently overwrite accepted local changes. Unknown references and unsupported
share principals quarantine new records. The importer never reads its visibility
oracle; `scripts/verify_export.py` compares the policy compiler independently.

This is a **snapshot import**, not arbitrary-org behavioral parity. Dynamic
sharing rules, formulas, source automations, binary attachments, subsequent
ownership/access recalculation and incremental synchronization need explicit
translation and validation before cutover. Imported access metadata records do
not themselves change live permissions. Keep the source export and perform an
access comparison for each organization; passing one fixture proves that fixture.

## Flows and workspaces

Enable the optional `flows_defs.crm` pack with `VEXA_FLOWS_DEFS_EXTRA` on both
Flows API and worker. `python -m crm.outbox --watch` uses the runtime credential,
`FLOWS_API_URL` and `FLOWS_API_KEY` to deliver `crm.record.changed` to `/events`.
The stable event ID makes redelivery idempotent. Failed or unhandled events remain
pending. Only tenant, record, revision and actor references leave CRM; no account
fields are copied into Flows. The initial pack records a workflow receipt. It
neither sends messages nor starts an autonomous agent with implicit CRM rights.

Native meetings retain their transcripts. Agent proposals cite the meeting ID
as evidence. Meeting linkage grants no CRM permission. Mounted workspaces retain
reusable strategy and playbooks, and link to native CRM records instead of holding
a second authoritative copy of account facts.

## Recovery and deployment scope

Back up the CRM database with PostgreSQL tools and verify restoration into an
isolated database before cutover. Revisions support reconstruction and audited
restoration by submitting the old values as a **new** change; there is no history
rewrite endpoint. No-CRM deployments retain existing workspace and meeting paths.
The development proof covers a Minutes Compose deployment; Helm/Lite distribution,
arbitrary source-org parity and production migration are separate acceptance work.
