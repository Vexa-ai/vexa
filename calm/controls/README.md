# calm/controls — control-requirement schemas

JSON-Schema 2020-12 definitions for the governance controls referenced by `../architecture.calm.json`
(via each control's `requirement-url`). One concern per file.

| File | Control |
|---|---|
| `single-writer.requirement.json` | P23 — a data carrier declares exactly one producer |
| `render-only.requirement.json` | a consumer renders, never re-derives a producer's data |
| `no-egress.requirement.json` | an edge carrying tenant data declares its egress posture (default: tenant-hosted) |
