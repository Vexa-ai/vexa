# core/workspaces — the workspace domain (PRD decision 47)

## Purpose

A workspace is a **user-owned git repo** (durable memory: `sg/` strategy graph +
`kg/entities/<type>/<slug>.md`) plus the identity, contract and portable primitives that make one
addressable — independent of which product surface reads or writes it. Carved out of `core/agent`
(ADR-0037 named the target; this domain is the carve happening) because "workspace" was a concept
four different governors (`core/agent/control_plane`, `core/agent/shared`, `core/agent/worker`,
`deploy/dogfood/rig`) each partially owned, with no single home.

**Hosted as a library, not a service** (P10): `core/workspaces` has no independent scale/runtime
force of its own — it has no life without `core/agent` in the room, which mounts it. Reconsider if
a paid domain later wants workspaces without agent.

## Status

This domain is being carved incrementally; each step lands on its own branch and leaves the product
working (see `~/dev/biz/drafts/2026-09-03-workspaces-domain-plan.md` for the full ordered plan).

- ✅ delivered (step 0) — the domain registered: `gate:domain-doors` UNITS entry, `manifest.py`
  DOMAINS entry, an empty `config.v1` + `routes.v1` declaration.
- ✅ delivered (step 1) — the `workspace.v1` contract (schema + goldens) moved here from
  `core/agent/contracts/`.
- ✅ delivered (step 2) — the portable primitives (`workspace_id.py`, `workspace_paths.py`,
  `entities.py`, `links.py`) moved here from `core/agent/shared/`; zero-HTTP, stdlib+git only —
  proof that a workspace can be read/written with no service around it ("local mode").
- ⬜ planned (steps 3-9) — the read surface, the id/link registry, the desk/flows-facing seam,
  attach/sync/publish/credentials, membership, and the cutover that makes `core/agent` a client of
  this domain through public functions instead of same-process imports.

## Contracts

**Owns:** `workspace.v1` (the user-workspace template + `EntityFrontmatter` convention).
**Consumed by:** `core/agent` (control plane + worker, today via same-process import — the P2
violation step 8's cutover retires).
