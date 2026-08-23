# behavior — SHOWCASE defaults only (the real voice is proprietary)

This directory holds the few PUBLISHED prompt/flow examples that demonstrate the capability.
The product's actual behavior domain — diverse, high-level, proprietary — is a PRIVATE content
tree mounted at `VEXA_BEHAVIOR_DIR` (the `_global` deployment pattern), resolved before these
files; flow params override both. Nothing proprietary belongs in this repo.

THE LAW: **machinery contains no prose.** Everything a human or an agent reads — prompts, email
copy, flow compositions, params — lives here and ships as data: hot-editable, versioned,
exportable, governed like content. Machinery (the engine, the step verbs) knows HOW; this domain
knows WHAT TO SAY.

- `prompts/` — the kickoff/instruction library. Steps resolve prompts by name via
  `ctx.flow.param("prompts")` overrides first, these files second — so a flow version can carry
  its own wording, and editing a file here changes behavior with zero deploys.
- Flow definitions live as rows (flows-api) with a lossless canonical-Python export.
- NEXT MOVE (coordinated — touches core/agent): the workspace seed relocates here; it is the
  product's voice, not agent machinery.
