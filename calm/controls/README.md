# calm/controls — control-requirement schemas

JSON-Schema 2020-12 definitions for the governance controls referenced by `../architecture.calm.json`.
Each builds on the CALM 1.2 control-requirement (`control-id`, `name`, `description`) and is named by
its repository path in each control's `requirement-url`. One concern per file.

| File | Control |
|---|---|
| `single-writer.requirement.json` | P23 — a data carrier declares the producers allowed to write it |
| `render-only.requirement.json` | a consumer renders, never re-derives a producer's data |
| `no-egress.requirement.json` | an edge carrying tenant data declares its egress posture (default: tenant-hosted) |
