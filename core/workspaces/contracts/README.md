# contracts — published by `workspaces`

Language-neutral contracts this domain owns (JSON Schema, read by path). Consumers reference these
by path; `gate:schema` validates goldens ≡ schema (P8).

| Contract | What it governs | Sealed |
|---|---|---|
| **`workspace.v1`** | the user-workspace template + `EntityFrontmatter` convention (the git knowledge graph `core/agent` reads/writes). Moved here from `core/agent/contracts/workspace.v1` (PRD decision 47, step 1). | **yes** |
