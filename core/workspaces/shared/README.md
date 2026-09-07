# workspaces · shared

The portable workspace primitives, imported by both `core/agent/control_plane` (the control plane)
and `core/agent/worker` (the isolated worker) — the two runtimes that read/write a workspace.

Zero-HTTP, stdlib + git only (no FastAPI, no pydantic): `workspace_id` (minting + the
`.vexa/workspace.json` identity file), `workspace_paths` (containment/traversal guards),
`entities` (the `EntityFrontmatter` read/write seam — `entity_upsert`, slugify, the card renderer),
`links` (the `[[ws:<workspace-id>/<entity-id>]]` grammar and cross-workspace resolution). Moved out
of `core/agent/shared` (PRD decision 47, step 2) as the proof that a workspace can be read and
written with nothing but a git checkout — no control plane, no worker, no service — the seam a
future local-mode CLI runs on.
