# modules — `agent`

Self-contained modules of the agent domain: a piece of the agent's behaviour with its own
dependencies, its own tests, and no connection or process of its own. A module is mounted by a
service; it never stands one up.

| Module | What it owns |
|---|---|
| [`context-stack`](context-stack/README.md) | the four layers a product-mode turn composes — global · group · personal · user-system — their membership and policy-routed writes, the owner's triage queue, and a write-only workspace-secret surface. |

The domain's other code sits beside this directory rather than in it: `control_plane/` (the
dispatcher and the terminal-side workspace machinery), `worker/`, `shared/`, `llm/`, `contracts/`.
