# agent · worker

The agent worker: runs a single agent workload to completion, inside a runtime-spawned container.
Spawned by the control plane; liveness is the workload's lifecycle.

- `engine.py` — the one generic turn engine. It reads its dispatch from the environment (the mounted
  workspace, the minted token, `REDIS_URL` and its unit's keys), runs each turn over
  `/workspace` through the [`llm`](../llm) `HarnessPort` (the CLI agent `VEXA_RUNNER` selects; no
  vendor name lives in this package), `XADD`s every UnitEvent to the out stream, then waits on the in
  stream for the next message until idle. It takes a follow-up from the in stream only when its
  signature verifies (unit.v1 `InputEntry`, `shared/unit_input`). Continuity is the session file in
  the workspace, so a reaped and respawned container resumes.
- `worker.py` — the `vexa-agent` entrypoint: a thin re-export of `engine`, plus the patchable
  `harness_factory` seam tests use. `__main__.py` runs `main()`.
- `jobs.py` — the background-job runner (`Vexa-ai/vexa#1584`): a marked act (Create, Extend, or
  anything the model hands to `spawn_job`) leaves the serve loop at once and runs on its own thread
  with its own harness session, so a two-minute act no longer holds the chat. It sits above the
  harness so that one implementation serves every runner. The contract is
  [`../llm/JOBS.md`](../llm/JOBS.md).
- `tool_access.py` — a turn and its tool access (P18): a vexa MCP call that fails at or after the
  attached delegation token's `exp` ends the turn with the typed `access_expired` fault instead of a
  silent tool error, and a turn whose work tree could not be handed to the tools user is refused
  before its harness starts (`tools_unconfined`), never run with those tools as root.
- `friction.py` — the agent side of the rough-edges loop: the rule in every turn's context, a client
  that posts a report to agent-api, and the reports the harness files itself when a turn ends on a
  tool error.
- `mcp_tools.v1.json` — the tools a delegated worker calls by name through the one MCP the gateway
  serves: its least-privilege allow-set.
- `Dockerfile` — the worker image.

**Redis.** A worker connects as a Redis user of its own unit (`control_plane/workload_redis.py`) and
touches four keys: it reads `unit:<id>:in` (read-only; agent-api writes it), appends to
`unit:<id>:out`, records how far it has read in `unit:<id>:cursor`, and reads its current delegation
token at `unit:<id>:delegation` (read-only; agent-api replaces it before it expires).

The live-meeting copilot (`worker.meeting`) and its completion port were removed by PRD decision 34: a
meeting reaches the agent over the MCP, on a person's turn.
