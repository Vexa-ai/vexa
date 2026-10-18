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

### Field classes and what a room reads (D4, 0.13.4)

**How it works.** Every field of every type has a class in `crm_field_classes` (`src/crm/field_classes.py`):
`private` (the record's owner only), `org` (the CRM policy decides, as before) or `ws` (also shareable to
the rooms of the listed workspaces). `$narrative` and `$match_ids` are classified like fields. The
migrations path seeds the stable types' classes (`bootstrap.DEFAULT_CLASSES`): contact history and
email-derived data private (`LastContact`, fields named like it or marked `x-source: email`,
`$match_ids`), company basics and the rest `org`. A field with no row takes the same rule, and a
property's `x-class` in its definition wins over the name rule. An administrator may change a row;
crm-api's role only reads the table. Every read path takes its fields from one place,
`Store._view`: get, search, name lookup, history, links, proposals, describe and card faces.

A **shared caller** (a room turn, a shared chat, later the `ws:` principal) is never an administrator,
whatever the person behind it holds. What it reads of a record is set by the organisation's
`room_fields` policy key. The default, `intersection` (interim, until a classification UI exists), gives
the fields every current member of its workspaces may read on that record (the roster the workspace
snapshot carries), minus private fields, minus fields shared only with other workspaces, plus fields
shared with this one. `classified` gives only the fields shared with this workspace. A narrative or
identifiers reach a room only when classified `ws` for it, and revision or proposal reasons and
evidence never do. `bound_to` lists only bindings inside the caller's ceiling unless an administrator
reads in person. Every audited operation records `principal` (`ws:<slug>` for a room), `on_behalf_of`
and `workspace`, and a search records the ids it returned.

**Why it complies.** A restricted field is absent from the answer, not nulled, so its absence is
indistinguishable from an empty field, and a filter on it is refused, so a search cannot reveal which
records hold a value. A room answers only what its least-entitled member could read, so asking the room
agent never widens anyone's view, and the owner's admin rights never reach it. The failure direction is
closed: a room with no roster, a member with no CRM membership, or an unreadable `room_fields` value
yields fewer fields, never more. Pinned by `tests/test_field_classes.py` and deny tests 3 and 18 in
`tests/test_sec_deny_2026_10_17.py`. **Open:** the audit row is still written after the operation in
its own transaction (H9 asks for the same transaction), and is not hash-chained.

### One Person, many addresses (D35, 0.13.4)

**How it works.** There is no persons table: the human is the organisation's Person record, and its
match identifiers say which addresses are theirs (`src/crm/persons.py`). A signed-in account resolves
to its Person by the address identity verified for it (`email:<address>`), and only failing that by
the record it is attached to. When that Person already has another account attached, the new one is
**linked**, not given a second record: an `account:<id>` match identifier on the same Person. The
picker shows the Person once, hinting "2 accounts?" until the person confirms. **Grants follow only
confirmed links** (coordinator ruling 2026-10-17): a card member here, or a chat participant or a
workspace member in agent-api (which reads `confirmed` from `POST /internal/persons`), reaches the
account picked and the accounts the person confirmed together with it, never an unconfirmed link. A Person with two or more linked accounts the person has not confirmed together gets a
**merge card**: a `crm_change_proposals` row with the statuses `merge_offered`, `merge_confirmed` or
`merge_declined`. Every linked account confirms it from its own signed-in session (`POST /person/merge`),
and the person chooses the primary address there; invites and nudges go to it. A person may also say
another address is theirs (`action: offer`); that card completes only when that address's account
confirms. Declining splits the Person: every account but the record's own goes back to a record of its
own, with its address. `POST /person/me` is the person's own view.

**Why it complies.** Nothing merges automatically, and no administrator, agent or worker can merge
accounts for a person: the card is refused to a delegated or internal caller, an account not on the
card is answered as if the card did not exist, `crm_review` cannot accept it (its statuses are not
`pending`), and `account:` identifiers cannot be written through `crm_change`. A merge needs every
account's own sign-in, so naming someone else's address grants nothing, and the `offer` answer is the
same whether or not that address has an account. Account links are match identifiers, so the `$match_ids`
field class (D4, private) keeps them from every other viewer and every room; the person's own view
shows their signed-in address in full and every other address masked (`v•••@example.org`). agent-api
shows other people only a count: the ids and addresses of accounts the viewer does not share a workspace
with never leave the CRM. Pinned by `tests/test_persons.py`. A wrong address that someone put in a Person's
match identifiers receives none of that Person's grants: links count for grants only after every login
confirmed them from its own session.

### Profiles are claimed Person records (D35, founder addition)

**How it works.** A signed-in person claims the Person record that matches their verified address in
their home org's CRM (`src/crm/person_profiles.py`, table `crm_profiles`, schema version 7). It happens
on its own at sign-in when exactly one record matches (a match identifier, or a Person whose `Email`
is that address); with several, `POST /person/profile` (`action: read`) lists them as a claim card and
`action: claim` takes the one the person picks. The claim is one-to-one: the row is keyed by the record
and unique per account, and a second login of the same human joins the claimed Person through the merge
card instead of claiming another record. The person's **profile fields** (display name, photo, headline,
company, bio, location, time zone, links to LinkedIn, GitHub, X or a website, contact preferences) live
on that row and are edited only by them (`action: edit`). The org's fields stay on the record, under the
CRM policy and the field classes. agent-api reads profiles for people who share a workspace with the
reader (`POST /internal/profiles`, the directory rule applied there). The public page `/p/<handle>`
(`action: publish`) is off until the person switches it on and shows only the fields they mark public
(`POST /internal/profiles/public`). `action: unclaim` keeps the record and the values, clears the
claim and the public page, and the fields are the org's again; that account is not claimed back on its
next sign-in. `action: import` is the seam for an importer the person starts (none ships).
**It is THE profile** (one writer): identity's earlier public profile (R4) accepts no more writes
(410, naming where it moved), answers reads marked `retired` for one release, and its page
`/user/directory/profiles/<handle>` answers 308 to `/p/<handle>`. On a claim, crm-api reads that old
profile once (`ProfileClient.legacy`, identity `GET /internal/users/{id}/legacy-profile`) and carries
the person's entries and public flags into the still-empty claimed profile, keeping the old handle;
the public page stays off until the person switches it on.

**Why it complies.** A claim needs the address identity verified, and moves nothing from the org to the
person: the profile starts empty, and the claim card shows only each candidate's name. No profile
answer reads the record's fields or narrative, so contact history, notes, deals and cadence can never
appear in a directory read or on a public page. An edit by anyone but the claimant, by a worker or by an
administrator is refused; an unclaimed profile is edited by whoever may write the record. Links are
https on the network's own domain, stored as given; nothing is fetched or imported. Pinned by
`tests/test_person_profiles.py`.

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
| — | POST `/person/profile` | The person's own profile: read, edit, claim, unclaim, publish, import (D35) |
| — | POST `/internal/profiles` · `/internal/profiles/public` | Profile fields for agent-api's directory; a published public page (internal secret) |
| — | POST `/person/me` · `/person/merge` | The person's own Person and merge card: confirm, decline, primary, offer (D35; a person's own credential) |
| — | POST `/internal/persons` | Each account's Person, its linked accounts and the primary address (internal secret; agent-api) |

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

Since event.v2 (PRD 0.13.4 D8) a revision's event also carries a fact envelope
(who it reaches: owner, visibility, audience, and a card's lifecycle fields kind,
state, next_actor, next_check_at, handoff). The outbox seals it with the `crm`
producer key derived from `VEXA_FACT_SEAL_SECRET` (falling back to
`INTERNAL_API_SECRET`); flows-api must resolve the same secret or it refuses the
fact 403 and the row stays pending. No title, summary or other field value is in
the envelope (`crm/fact_envelope.py`, vendored from `core/flows/contracts/event.v2`).

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
