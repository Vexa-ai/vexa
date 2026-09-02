# llm — the detached LLM + agent-harness module

Everything vexa knows about coding-agent CLIs lives HERE, behind one provider-agnostic port.
Product code (chat, routines) imports only the front door (`llm/__init__.py`) and never names a
vendor.

## The port (one call shape)

| Port | Call shape | Used by | Selected by |
|---|---|---|---|
| `HarnessPort` | a CLI coding agent over the mounted workspace — tool loop, sessions, streamed UnitEvents | every agent turn: chat, routines, flows | `VEXA_RUNNER` |

There was a second, `CompletionPort` — a plain prompt→text HTTP call selected by
`VEXA_LLM_PROVIDER` — whose only caller was the live meeting copilot's card beats. **PRD decision
34 removed that pipeline**, and the port, its three adapters (`openai_compat.py`,
`anthropic_api.py`, `claude_cli.py`) and every `VEXA_LLM_*` variable went with it. The product runs
no model calls of its own beside the agent.

`HarnessPort` is a `typing.Protocol` (duck-typed, mirroring `core/runtime`'s `Backend` port);
adapters are selected env-driven in `registry.py` and constructor-injected everywhere, so tests use
trivial fakes.

## Adapters

- **Harnesses**: `claude_code.py` (the `claude` CLI — stream-json + open-stdin steering) · `codex.py`
  (Codex app-server JSON-RPC — durable threads + `turn/steer`). Both normalize into the same frozen
  UnitEvents; Claude remains the deployment default.

Raw `httpx`, no vendor SDKs — the protocols are ~10 lines each and a pinned SDK is a heavier
supply-chain surface than the dialect itself.

## Configuration

| Env var | Meaning | Default |
|---|---|---|
| `VEXA_RUNNER` | harness adapter key: `claude-code` \| `codex` | `claude-code` |
| `ANTHROPIC_*`, `HOST_CLAUDE_CREDENTIALS` | claude-code adapter ONLY | — |
| `HOST_CODEX_CREDENTIALS`, `OPENAI_API_KEY` | codex adapter subscription-file / API-key auth | — |

## Rules

- **This module imports NOTHING from product code** (`shared/`, `contracts`, `worker/`,
  `control_plane/`) — it must stay liftable into a standalone brick.
- Vendor names appear only in adapter files (`claude_code.py`, `codex.py`), never in
  `ports.py`/`registry.py` beyond registry keys.
- UnitEvent shapes (`message-delta` / `tool-call` / `tool-result` / `done{reply,sessionId,ok}` /
  `commit` and the `model-error` / `auth-error` builders in `errors.py`) are FROZEN — the terminal
  reducer and SSE relay consume them field-for-field. They describe the AGENT harness; a meeting's
  feed carries the transcript and nothing else.
- Session ids are OPAQUE per-harness tokens; an alien/stale id must yield `done.ok=False` (the
  engine's stale-resume retry heals it).

## Adding a runner

1. New adapter file implementing the port (copy the closest existing one).
2. One line in `registry.py`'s table.
3. Unit test with a fake `exec_fn` — see `tests/test_llm_claude_code.py`.

## Codex subscription authentication (compose)

Codex app-server can use the host's ChatGPT subscription login without copying credentials into
the repository or workspace:

1. On the Docker host, install/run Codex and complete `codex login`. Verify
   `~/.codex/auth.json` exists and remains owner-readable only (`0600`).
2. In the ignored `deploy/compose/.env`, set:

   ```dotenv
   VEXA_RUNNER=codex
   VEXA_MIDTURN_INJECT=1
   HOST_CODEX_CREDENTIALS=/absolute/host/path/to/.codex/auth.json
   ```

3. Rebuild `agent-worker` after changing the pinned Codex version, then recreate `runtime` and
   `agent-api`. The runtime bind-mounts only that file at `/root/.codex/auth.json:ro` in each worker.

The adapter keeps rollout history under the private continuity mount's already-ignored
`.claude/codex/sessions/`; the subscription auth file stays in `/root/.codex` and is never copied,
staged, emitted, or returned through the workspace API. `VEXA_CODEX_MODEL` is optional; leaving it
empty uses the subscription account's Codex default and deliberately ignores an inherited
`claude-*` model pin.
