# roadmap — the Vexa EI runtime docs site (Mintlify)

Product- and contributor-facing documentation for Vexa's Enterprise Intelligence runtime, rendered by
Mintlify (`docs.json` is the nav config; `index.mdx` the landing page). It is **complementary** to
`docs/ARCHITECTURE.md`: ARCHITECTURE is the governing build reference (rules + gates), this site explains
the vision, primitives, and capabilities at a consumer altitude.

- `foundations/` — vision · landscape · principles · the model (the "why")
- `primitives/` — the irreducible building blocks: workspace · agent · runtime · scheduler · integration · stream · identity
- `architecture/` — how dispatch / execution / governance / streaming / trust actually work
- `core/` — the backend domains (agents · runtime · meetings)
- `capabilities/` — the feature inventory (meetings, routines, knowledge, chat, …)
- `api/` — the OpenAPI surfaces (agent · meetings)
- `roadmap/` — the staged delivery plan (approach · stages · status)
- `concepts.mdx` · `deployment.mdx` — glossary + self-host
