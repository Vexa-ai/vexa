# MIGRATION-0009: the workspace principal (`principals`)

**Status:** in the SSOT model (`schema/models.py` `Principal`). `ensure_schema` creates it in-band
as a new, empty table. A row is made the first time agent-api asks for a workspace's principal,
on that workspace room's first turn (`VEXA_ROOM_WS_PRINCIPAL`, on by default).

## What it adds

| Object | Shape | How it lands |
|---|---|---|
| `principals` | `id` (PK, FK → `users.id` ON DELETE CASCADE), `kind` (`'workspace'`), `workspace_id` (unique), `bills_to` (FK → `users.id` ON DELETE RESTRICT), `status` (`'active'`), `created_at` | `CREATE TABLE` (new, empty) |

## How it works

- A room's turn runs as its workspace's agent (PRD 0.13.4 D2). `POST /internal/principals/workspace`
  is internal-tier and idempotent: `{workspace_id, bills_to}` returns the workspace's one principal.
  - On the first ask it writes a dedicated `users` row: email `ws-<id>@principal.invalid`,
    `max_concurrent_bots` 0, `data.principal_kind = "workspace"`.
  - It also writes the `principals` row whose id is that account's id.
  - When a new payer asks, it moves `bills_to`.
- Kind `person` is implicit: an account acting as itself writes no row. There is no persons table;
  the human is the CRM person record.
- `/internal/validate` answers a delegation token that names `principal_kind: "workspace"` only
  when the row exists, is `active`, is of kind `workspace` and names the token's workspace.
  - `user_id` is the principal's own id.
  - `delegation.bills_to` is the payer.
  - `person_is_admin` is false.
- An API key on a principal's account is refused, and nothing mints one.

## Why it complies

| Rule | How | Enforced by |
|---|---|---|
| P20 | A room's token names an account that owns nothing of a person's. Its claims must agree with the table. | `tests/test_workspace_principal.py` |
| P4 | `identity.v1` `ValidateDelegation` and `delegation.v1` `Claims` gain optional fields only, re-sealed. | `gate:schema`, `gate:contract-version` |
| P23 | identity is the table's one writer. | `gate:dataflow` |
