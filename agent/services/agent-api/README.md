# agent-api — the agent control plane (Python)

Turns a **`transcript.v1`** input into a **governed action** committed to a user **`workspace.v1`**
git repo, spawning the sandboxed worker via **`runtime.v1`** (profile `agent`). Python because the
agent domain is the LLM/tooling + runtime ecosystem (P13); structured to mirror the `runtime/`
kernel so `gate:python` covers it (ADR recommended — "agent domain is Python; consumes transcript.v1
as the seam").

## Shape (hexagonal — P5)

```
src/agent_api/
  config.py      P14 — VEXA_* env, pydantic-settings, fail-fast; secrets are SecretStr
  models.py      the agent's own shapes (AgentAction, WorkspaceWrite, AgentRunRequest/Result)
  ports.py       PURE protocols: WorkspacePort · RuntimePort · TranscriptSource
  contracts.py   load transcript.v1 + workspace.v1 schemas BY PATH and validate (P4)
  core.py        the agent-run skeleton: transcript → stub action → (would-)commit  [LLM = TODO seam]
  spawn.py       build the runtime.v1 worker `env` (matches golden spec-agent.json)
tests/           L1 contract-consumer · L2 unit (ports faked) · config
```

## The seams (what this service is coupled to)

| Seam | Direction | How |
|---|---|---|
| `meetings/contracts/transcript.v1` | **consumes** | JSON Schema read **by path** + jsonschema-validated — never imports meetings code (`meetings ⊥ agent`) |
| `agent/contracts/workspace.v1` | **produces** | emitted entity frontmatter validated against the schema before any write (P8) |
| `runtime/contracts/runtime.v1` | **spawns over** | the worker is a `runtime.v1` workload; repo URL + scoped token travel in its `env` |

## This increment

Proves the **contract wiring**, not the LLM. `core.run()` reads a `transcript.v1` golden, emits a
deterministic stub `upsert_entity` action (a `meeting` entity), validates it against `workspace.v1`,
and commits it through the `WorkspacePort`. The LLM + tooling loop is a clearly-marked `TODO(LLM seam)`
in `core.py` that slots in behind the same `AgentAction` shape. Real adapters (git, an HTTP client to
runtime.v1, a redis transcript stream) are deferred — the ports exist now (P16).

## Run the tests

```bash
uv run pytest -q        # L1 contract + L2 unit; uv manages this package's own venv/deps
```

## Deferred (ports now, impl later — ADR-0003)

Per-tenant envelope encryption + a brokered/scoped identity for the worker (P15) sit behind
`WorkspacePort` / the identity token; this increment threads the seam, not the implementation.
