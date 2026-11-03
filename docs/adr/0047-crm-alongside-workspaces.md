# ADR 0047 — A CRM alongside workspaces: stable entities across the organisation

**Status:** accepted · 2026-10-13 · founder direction (2026-10-12, 2026-10-13) ·
[#1783](https://github.com/Vexa-ai/vexa/issues/1783) · decision 6 is a default the founder may strike ·
builds on ADR-0036/0037 (one MCP assembled at the gateway from domain manifests), ADR-0040 (the
credential broker), ADR-0041 (signed identity), ADR-0046 (flows react to events) and ADR-0048
(sessions, the leak rule) · applies P3, P11, P18, P20 and P23

## Context

Workspaces are git repositories a group shares: notes, opinions, strategy, playbooks. They are good
at knowledge and bad at state that many groups share about one company or one person — the owner,
the stage, the last contact, what we promised. That state today is either copied into several
workspaces (and drifts) or kept outside the product.

The founder's direction: *"we also want crm alongside workspaces so we could have stable entities
across organization as well as workspaces so this way we have both crm and workspaces"* (2026-10-13);
earlier, an entity registry which is the CRM, *"the access is workspace membership + crm access"*
and *"crm should backlink to workspaces mentioned"* (2026-10-12).

A working prototype exists (records with typed fields, per-person policies with record-type and
field scopes and per-record grants, quarantine, deny answered as not found, import, proposals and
review, card layouts, a link parser for `/crm?record=<id>`, a relationships table, and the MCP tools
`crm_describe` / `search` / `read` / `history` / `change` / `review` / `configure`). This ADR fixes
how it sits beside workspaces and is the decision the module is built against.

## Decision

1. **Two stores side by side, one writer each.** Workspaces (git) hold a group's knowledge: notes,
   opinions, strategy. The CRM holds the organisation's shared state about entities: fields, owner,
   stage, relationships, activities, commitments, last contact. A fact has one home. The CRM is an
   optional module (`core/crm`, service `crm-api`); a deployment without it loses no workspace,
   meeting, agent or flow behaviour, and its tools and page are absent rather than broken.

2. **Organisation is the top level.** It owns the registry, the people directory and the CRM
   policies. An install is one organisation for now (an enterprise install is one organisation);
   every CRM row still carries an `org_id` (`CRM_ORG_ID`, fixed per instance, never chosen by a
   caller) so the cloud can hold many later. Workspaces sit inside the organisation.

3. **Stable entities across the organisation.** People, companies, deals and projects (meetings
   link to them) each have one id, plus match identifiers (emails, domains, calendar event ids).
   Duplicates merge (`merged_into`; reads of a merged id answer the surviving record), and a rename
   carries everywhere because pages hold the id, not the name. **A user is a person entity with an
   account attached** (`account_id`); external people are person entities with none. The account is
   attached on sign-in — the first authenticated CRM call and the `onboarding.completed` fact both
   do it — by account id, else by the account's verified email among a person's match identifiers,
   else by creating the person.

4. **Workspaces refer, never copy.** A page links a record (`[label](/crm?record=<id>)`) or names
   it in front matter (`entity: <id>`). Shared fields render on the page as a generated block at read
   time, never stored in the file.

5. **Backlinks.** Each link becomes a reference *"page P in workspace W refers to record R"*. agent-api,
   which holds the checkouts, publishes `workspace.references.changed` for a workspace whose head
   moved — the whole set of that workspace's references at that commit — and the CRM flow pack hands
   it to the CRM, which replaces the workspace's set. Because each fact is a full snapshot of a
   commit, it is idempotent and the CRM's references are rebuildable from git at any time
   (`python -m control_plane.crm_references rebuild` in agent-api). A record shows *Mentioned in*. **A person sees backlinks only
   from workspaces they are a member of** — others are omitted entirely, with no count. An admin sees
   all. The same rule holds in every view and in every agent tool answer.

6. **Access: two gates, neither widens the other** *(default; the founder may strike)*.
   - The CRM policy is the authority: active, admin, per record type a read and write scope
     (`all` · `own` · `none`) with readable and writable fields, per-record grants, and imported rules.
   - In addition an admin may **bind a workspace to records**. Members of that workspace then get
     derived record grants — a reader of the workspace may read the record, a contributor or owner
     may write it. The CRM derives them from identity's live membership answer — the workspaces a
     person reads and the ones they may write, narrowed to a worker's ceiling — so they end the moment
     membership ends, with no second step. The roster carried on decision 5's fact decides only whose
     desk a page is on.
   - A link never grants access. A binding never widens a field restriction a policy sets. Deny
     answers not found. A person restricted to some fields gets no free-text narrative, history
     reasons or evidence.

7. **Agents.** An agent acts with its owner's CRM rights, narrowed by the job's ceiling: its derived
   grants come only from workspaces inside the ceiling, and a worker with no person in the loop may
   read and propose but not write, review or configure. In a session other people read, the leak rule
   of ADR-0048 decision 4 includes CRM records: **such a turn may use only records bound to a
   workspace every participant can read** (the turn's ceiling is already that intersection), and a
   record outside it is refused with `shared_conversation`, so the agent offers the speaker's private
   chat. Agents **propose** changes; a person accepts or rejects them with their own credential;
   policy may allow an agent to write named fields directly (`agent_direct_fields`). Every change
   keeps its history, and every read and write is audited with the session's trace id.

8. **MCP first.** The CRM's capabilities are agent tools in the one gateway MCP, through the CRM's
   own domain manifest (`core/crm/mcp.tools.v1.json`, domain `crm`): `crm_describe`, `crm_search`,
   `crm_read`, `crm_history`, `crm_change` (a proposal by default for agents), `crm_review`,
   `crm_configure` and `crm_bind`. Refusals are typed. Dedicated UI exists only where a tool cannot
   do the job: the record page (fields, history, *Mentioned in*, pending proposals with accept and
   reject).

9. **Out of this change, listed:** groups imported from an identity provider; a central
   relationship-based check (OpenFGA-style); the People view; @-mentions of entities; import
   connectors beyond the prototype's; a multi-organisation cloud.

## Storage

The CRM's tables (`crm_*`) live in the instance's Postgres and are reached as the CRM's **own role**
(`vexa_crm` by default) — a login with no admin power and grants on the CRM's tables only, created
with the admin credential by the migrations path (`python -m crm.migrate init`: compose `crm-init`;
the Helm chart does not carry the CRM yet). `crm-api` never holds the admin credential and runs no DDL: it refuses a
superuser login and any schema version but its own. Revisions and card layouts are append-only, by
trigger. This is ADR-0048's own-role pattern.

## Alternatives

- **Entities as workspace pages.** Rejected: a company page in each workspace is a copy per group,
  and access to a page is access to the whole workspace.
- **A CRM inside a workspace (`_global/crm/`).** Rejected: field-level access and per-record grants
  cannot be expressed as git permissions, and every group would read every record.
- **Access by link (a page that mentions a record grants it).** Rejected: a link is a reference, not
  a permission, or anyone with a workspace could grant themselves any record by writing its id.
- **Membership mirrored into CRM policy rows.** Rejected: two writers of one fact. Derived grants
  are computed from membership, never stored as grants.

## Consequences

- An entity has one place to change, and every workspace that mentions it shows the current value.
- The CRM learns about pages only through agent-api's fact; a deployment with flows off has no
  backlinks until the rebuild runs, and says so (the references count is part of `crm_read`).
- A shared conversation knows less about entities than either person's private chat — the same price
  ADR-0048 pays for workspaces.
- An agent cannot accept its own proposal; a person does, on the record page or with their own key.
