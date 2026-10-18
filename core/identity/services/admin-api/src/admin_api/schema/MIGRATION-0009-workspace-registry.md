# MIGRATION-0009: the workspace registry (`workspaces`, `memberships`, `invites`)

**Status:** in the SSOT model (`schema/models.py`); `ensure_schema` creates the three tables in-band
(`create_all`, new and empty). No existing table is altered. PRD 0.13.4 D5.

## Why

A shared workspace's roster, visibility and connections lived in its own git tree
(`policy/*.json`), the directory a contributor's page editor writes and their agent runs in. On
2026-10-17 a contributor's page write to `policy/members.json` made them owner at once (security
review C3). Membership leaves git: identity is the one writer of who may enter a workspace, git keeps
content only, and `policy/` rule files stay platform-only.

## What it adds

| Table | Shape |
|---|---|
| `workspaces` | `id` (PK, the key memberships and invites name; today agent-api's slug), `slug` (unique), `org_id` (`'default'`), `owner`, `visibility` (`open`/`closed`), `kind` (`normal`/`pair`), `connections` (JSONB list), `archived_at`, `updated_at` |
| `memberships` | PK (`principal_id`, `resource`); `resource` is `workspace:<id>` or `room:<id>` (closed rooms only); `role` (`viewer`/`contributor`/`owner`), `added_by`, `added_at`, `position` (roster order), `data` (display extras: `email`, `discoverable`) |
| `invites` | `id` (PK), `resource`, `token_hash` (unique, sha256 only), `role`, `mode`, `max_uses`, `uses`, `expires_at` (epoch), `created_by`, `created_at`, `revoked`, `data` (`allowed_emails`, `owner_act`) |

There is no persons table: a person is their CRM record, and a membership points at an account
(a principal). `principals` (the workspace principal `ws:<id>`) is S1-B's.

## How data arrives

agent-api moves it, not this service: at start (`workspace_membership.migrate_policy_store`) and on
the first read of any workspace not yet here, it copies the workspace's interim file store
(`<workspaces>/.policy/<ws>/`) or, failing that, its tree's `policy/{members,visibility,connections}.json`,
plus its invites (`.invites/<ws>.json` or the tree's `policy/invites.json`), through
`PUT /internal/registry/workspaces/{id}` and `/invites`. Reads stay compatible for one release;
`VEXA_POLICY_LEGACY_READ=0` on agent-api ends them.

## Doors (internal tier, no dev bypass: `app/workspace_registry.py`)

`GET|PUT|DELETE /internal/registry/workspaces/{id}` · `GET /internal/registry/principals/{id}/memberships` ·
`GET|PUT /internal/registry/workspaces/{id}/invites` · `GET /internal/registry/invites/{token_hash}`.
A roster or an invite list is replaced whole in one transaction.

## Safe on a live database

Three new empty tables; nothing existing is rewritten or locked beyond `CREATE TABLE`. Old code never
reads them. The `users.data.memberships[]` index stays as it was (agent-api still mirrors into it).
