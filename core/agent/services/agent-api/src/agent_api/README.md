# agent_api — the package (public surface = `__init__`)

The agent control plane core, hexagonal (P5). Public surface is `agent_api/__init__.py`:
`run`, `Settings` / `load_settings`, the models (`AgentRunRequest`, `AgentAction`, …), the ports
(`WorkspacePort`, `RuntimePort`, `TranscriptSource`), and `build_worker_env`.

| Module | Concern |
|---|---|
| `config.py` | `VEXA_*` env → validated `Settings` (P14); secrets as `SecretStr` |
| `models.py` | the agent's own shapes (not a published contract) |
| `ports.py` | pure protocols the core depends on — the adapter seams (P5) |
| `contracts.py` | load + validate `transcript.v1` (consumed) / `workspace.v1` (produced) by path (P4) |
| `core.py` | `run()` — transcript → stub action → would-commit; LLM is a marked TODO seam |
| `spawn.py` | build the `runtime.v1` worker `env` |

Depends only on: its own code, `pydantic` / `pydantic-settings` / `jsonschema` / `referencing`, and
the published schemas (read by path). Never imports another domain's internals.
