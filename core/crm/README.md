# Optional CRM record domain (ADR-0047)

The organisation's stable entities — people, companies, deals, projects and imported types — beside
workspaces. It does not own meeting transcripts, workspaces or workflow execution, and a deployment
may omit it entirely: its tools, record page and flows step are then absent. Operator and user guide:
`docs/docs/minutes/crm.mdx`.

## Run and test

```sh
uv run --project core/crm pytest core/crm/tests -q
```

`python -m crm.migrate init` (compose `crm-init`, admin `DB_*` plus `CRM_DB_USER`/`CRM_DB_PASSWORD`/
`CRM_ORG_ID`) creates the CRM's own role, the `crm_*` tables and the organisation with its entity
types. `python -m crm` (crm-api, port 8300) logs in as that role with `CRM_DATABASE_URL`, and needs
`ADMIN_API_URL`, `INTERNAL_API_SECRET` and `CRM_ORG_ID`; it refuses a superuser and any other schema
version. Optional `CRM_AGENT_API_URL` is agent-api's door, where crm-api reads `_global/POLICIES.md`
for the desk's limits (`src/crm/policies.py`: narrow-only, cached 30 s, the defaults when unset or
unreachable). Contract: `contracts/crm.v1`. Tools: `mcp.tools.v1.json`.

### Personal CRM (individual accounts, `personal_crm`, off by default)

**How it works.** `CRM_ORG_ID` serves everybody. With `VEXA_FEATURES` naming `personal_crm` (explicit:
`all` does not switch it on), crm-api asks identity for each person's home org
(`GET /internal/users/{id}/org`, cached 30 s, `src/crm/orgs.py`). A person whose home org is their own
org of one (`p-<id>`, kind `personal`, made by identity's `POST /internal/users/{id}/personal-org`)
is served from that org's rows only. The org is created on first use with the person as its only
administrator. Everyone else is served from `CRM_ORG_ID`, as before.

**Why it complies.** The org comes from identity, never from the request: the `X-CRM-Org` binding
stays the install's, and a body naming an org is refused (422). Every query carries the org key, so
a company record answers an individual `not_found`, and the reverse holds too. If identity cannot
answer, the request fails with 503; it never falls back to the company's CRM.
`tests/test_personal_crm.py` pins all of this.

**On Postgres, as crm-api's own role.** That role cannot write orgs, policies or definitions, nor
delete most rows. So `python -m crm.migrate init` also creates two SECURITY DEFINER functions and
grants the role EXECUTE on them only: `crm_bootstrap_personal_org` (first use) and
`crm_erase_personal_org` (account delete, `POST /internal/orgs/p-<id>/erase`). Both accept an org of
one (`p-<id>`) only. **The erase leaves the append-only tables** (revisions, card layouts, the audit,
desk layout versions) until their erasure is decided (`migrate.ERASE_APPEND_ONLY`): it deletes
everything else, keeps the records those revisions name as empty, deleted rows, and answers
`{rows, removed, tombstoned, pinned, left}`. `tests/test_pg_runtime_role.py` proves both as the real
role on a real Postgres; it is skipped unless `CRM_TEST_PG_ADMIN_URL` names a throwaway one (its
docstring has the three commands).

### A room with people from outside the org (`scope.audience: cross_org`)

Every answer to such a turn is projected: record fields to their public ones (`store.PUBLIC_FIELDS`
plus the type's `public_fields`), and the envelope of a read, history, search or name lookup to an
allow-list of keys (`store.CROSS_ORG_KEYS`; any other key is empty). A search may filter on public
fields only. A card answers its title, no movement, activity, members or boards, and the card list
no proposals. Pinned by `tests/test_cross_org_audience.py` and `tests/test_cross_org_answers.py`.

## Operations

| MCP tool | HTTP | Purpose |
|---|---|---|
| `crm_describe` | POST `/describe` | Readable record types and fields |
| `crm_search` | POST `/search` | Authorized, filtered, paged records |
| `crm_read` | POST `/read` | Fields, links, proposals, `mentioned_in`, `bound_to`, match identifiers |
| `crm_change` | POST `/change` | Create, update, propose or merge; an agent's update is a proposal |
| `crm_history` | POST `/history` | Revisions, filtered by current access |
| `crm_review` | POST `/review` | Accept or reject — a person's own credential only |
| `crm_configure` | POST `/configure` | Card layouts (administrators) |
| `crm_bind` | POST `/bind` | Bind a workspace to a record (administrators) |
| `desktop_read` · `desktop_arrange` · `desktop_arrange_propose` | POST `/desk/read` · `/desk/arrange` · `/desk/arrange/propose` | The person's own desktop (feature `desktop`); `/desk/proposal` and `/desk/undo` are the person's |
| `workspace_settings_read` · `workspace_settings_set` | POST `/desk/workspaces/read` · `/desk/workspaces/set` | The person's own settings per workspace on the `member_of` relation (`crm_member_settings`, `src/crm/member_settings.py`), in the caller's own CRM org: pinned, hidden, muted, notify, room order, level, collapsed. Rides `desktop`; direct writers only, else `person_only`; a shared turn reads none. Schema version 5 |
| `card_list` | POST `/card/list` | Cards: a workspace's board, the person's own; the proposals waiting for them (feature `desktop_cards`) |
| `card_propose` | POST `/card/propose` | Propose a new card — a person accepts it with `crm_review` |
| `card_update_propose` | POST `/card/update/propose` | Propose a change or a stage move — a member accepts it |
| — | POST `/card/read` · `/card/create` · `/card/update` · `/card/members` · `/card/membership` | The person's own card verbs: read with activity, make, change or move, share, accept being added |
| — | POST `/internal/references` | A workspace's references and roster at one commit (internal secret) |
| — | POST `/internal/accounts` | Attach an account to its person (internal secret) |

**Cards (desktop D2, `src/crm/card_ops.py`).** A card is a record of a card type (`"x-card": true`;
bootstrap adds `Card`). Its `State` stays on the lifecycle `_global/POLICIES.md` declares
(`lifecycles.card`, default `To do > Doing > Done | Dropped`) on every write path, and an agent's
stage change is always a proposal. Members are `crm_card_members` (invited, then member); a workspace's
board is its bindings; a sub-card names its `Parent`; a proposed card is a change proposal with no
record yet. Faces are sanitised and resolved per viewer by `src/crm/card_face.py`, vendored verbatim
from `core/agent/contracts/blocks.v1/card_face.py` (parity fact `card-face`). Schema version 4.

Every caller is resolved by identity's `/internal/validate` (memberships, delegation, shared turn);
every route is audited in `crm_audit`.

## Import

`PYTHONPATH=core/crm/src uv run --project core/crm python core/crm/scripts/import_export.py
<export-directory> --org <org> --actor <operator-subject> --user-map <json>`
loads describe JSON and CSV exports using the migration credential. The map is
source User ID to an **existing** Vexa subject ID; login credentials are never
copied from the source. Operator membership is provisioned separately in
`crm_access_policies`. Source IDs are namespaced by org and source org.

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
pending. Only org, record, revision and actor references leave CRM; no account
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

## Configurable cards

Minutes uses the shared Markdown renderer with readable labels, formatted values
and permitted record links. Technical fields are collapsed under All fields.
Administrators call `crm_configure(object_type)` to read the current version,
then supply `expected_version`, `reason` and `layout` to save:

```json
{"title_field":"Name","sections":[{"title":"Overview","fields":[
  {"field":"Amount","label":"Investment","format":"currency","currency":"USD"},
  {"field":"Description","format":"markdown"}
]}],"show_narrative":true,"show_related":true}
```

Formats: text, markdown, number, currency, percent (0–100), date and badge.
Layouts reference existing fields; no executable templates. `crm_read` filters
configuration by field permissions. Reopen or refresh the card after configuring.
Layouts apply to all records of that type in the configured org.
Run the CRM migration to add `crm_card_layouts`, then grant runtime SELECT and
INSERT on that table only. Layout revisions are append-only, independent of
record history. Stale layout versions return 409. Core Vexa tables stay unchanged.
