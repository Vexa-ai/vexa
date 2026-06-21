# services — `agent` domain (deployment units)

Processes with a lifecycle and an address (C4 "container"). Currently:

- **`agent-api/`** — the agent control plane: turns a `transcript.v1` input into a governed action
  committed to a user workspace (`workspace.v1`), spawning the sandboxed worker via `runtime.v1`.

Each service is a modular monolith internally (hexagonal — core + ports + adapters) and may import
only its own code, `runtime/contracts`, and `meetings/contracts` (the published `transcript.v1`
seam). It may **never** import `meetings/` internals (`meetings ⊥ agent`).
