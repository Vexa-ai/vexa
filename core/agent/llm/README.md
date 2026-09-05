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
  (Codex app-server JSON-RPC — durable threads + `turn/steer`) · `openai_agent.py` (**ours** — an
  agent loop over any OpenAI-compatible `chat/completions` with function calling, no CLI and no
  vendor SDK). All three normalize into the same frozen UnitEvents; Claude remains the deployment
  default.

### The runner matrix

| `VEXA_RUNNER` | What drives the turn | Tools the model gets | Sessions | Steering |
|---|---|---|---|---|
| `claude-code` (default) | the `claude` CLI, `--output-format stream-json` | the CLI's own (Read/Write/Edit/Bash/Web...) + MCP via `--mcp-config` | CLI transcripts under `.claude/projects` | mid-turn stdin injection |
| `codex` | Codex app-server over JSON-RPC | Codex's own + MCP | durable threads | `turn/steer` |
| `openai-agent` | **this repo's loop**, raw httpx to `POST {base}/chat/completions` | `Read`/`Write`/`Edit`/`Glob`/`Grep` implemented here, sandboxed to the mounts, `WebSearch`/`WebFetch` (`web_tools.py`), plus every MCP tool in the same `mcp.json` (http **and** stdio) | JSONL written in the CLI's on-disk shape, so `workspace_reader.history` reads it unchanged | none (one request at a time) |

`openai-agent` exists for PRD decision 37: run the service on a model we host. It has **no `Bash`
and no skills discovery** — a name in the allow-set it does not implement is simply not attached. It
carries a hard per-turn budget (tool calls + wall clock) and trims context oldest-tool-result-first,
because the box it was built for holds ~29 requests at 24k context. Qwen on that box needs
`VEXA_LLM_EXTRA_BODY={"chat_template_kwargs":{"enable_thinking":false}}` or it spends the whole
budget reasoning.

### Web reach — an adapter, never a dependency (`web_tools.py`)

`WebFetch` is always attached; `WebSearch` is attached only when the operator has named a search
endpoint. **No search engine ships with Vexa** — no image, no compose service, no vendored code —
and that is a licence decision as much as a deployment one: the obvious self-hosted metasearch is
AGPL-3.0. The interface is `VEXA_SEARCH_URL` + `VEXA_SEARCH_DIALECT` (`searxng` | `brave`), and a
third dialect is one ~20-line function in `_DIALECTS`: take a client, a URL, a query and a count,
return `[{"title","url","snippet"}]`.

`WebFetch` carries the guard search does not need — a URL the MODEL chose is an outbound destination
picked by a non-operator — so it refuses loopback / link-local / private / reserved targets and
re-checks every redirect hop, exempting only the operator's own `VEXA_SEARCH_URL` host. The rule is
`control_plane/model_endpoint.py`'s, **re-stated rather than imported**: the worker image ships
`worker/`, `llm/`, `shared/` and `contracts/` and deliberately not `control_plane/`, so an import
would be an ImportError in the only process that runs this code.

Raw `httpx`, no vendor SDKs — the protocols are ~10 lines each and a pinned SDK is a heavier
supply-chain surface than the dialect itself.

## Configuration

| Env var | Meaning | Default |
|---|---|---|
| `VEXA_RUNNER` | harness adapter key: `claude-code` \| `codex` \| `openai-agent` | `claude-code` |
| `VEXA_LLM_BASE_URL` | openai-agent endpoint | **required** for `openai-agent` (falls back to `ANTHROPIC_BASE_URL`) |
| `VEXA_LLM_API_KEY` | openai-agent credential (optional for local runtimes) | falls back `ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY` |
| `VEXA_LLM_MODEL` | openai-agent model (free string) | empty → fail-loud at the first request |
| `VEXA_LLM_EXTRA_BODY` | JSON object merged into EVERY openai-agent request | `{}` |
| `VEXA_AGENT_MAX_TOOL_CALLS` / `VEXA_AGENT_MAX_TURN_SEC` | openai-agent per-turn budget | 40 / 900 |
| `VEXA_AGENT_CONTEXT_TOKENS` | openai-agent context ceiling (trims oldest tool results first) | 24000 |
| `VEXA_AGENT_STREAM` | openai-agent SSE streaming (`0` = one blocking request) | `1` |
| `VEXA_SEARCH_URL` | operator-supplied search endpoint for `WebSearch` | empty → `WebSearch` is not attached |
| `VEXA_SEARCH_DIALECT` | wire format of that endpoint: `searxng` \| `brave` | `searxng` |
| `VEXA_SEARCH_API_KEY` | credential for that endpoint (brave needs one; searxng does not) | empty |
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
