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
- **Claude Code's skills**: `claude_skills.py` stages the turn's skill set into the worker's user
  scope (`~/.claude/skills`) — the platform's governed skills, then the workspace's own with the
  tool-granting frontmatter removed. A workspace skill is staged as a copy of its regular files,
  each opened without following a link: a link, a FIFO or a hard-linked file is left out, and a
  skill whose `skills/` folder, skill folder or `SKILL.md` is a link, or that holds a second
  `SKILL.md` below its top, is not staged. Part of the `claude-code` adapter.
- **Panel events**: `tool_events.py` — the closed tool vocabularies and the event a successful
  result earns (a write opens its file, a bot send opens the transcript, `open_page`, chips, a
  workspace joining the chat). Imported by all three harnesses so a turn paints the same screen
  whichever one runs.

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
endpoint. **No search code ships in this module or inside any `vexaai/*` image** — the backend is
whatever endpoint the operator names. Compose can start one for them: an OFF-by-default `search`
profile running an unmodified, digest-pinned SearXNG (AGPL-3.0) as a sidecar the OPERATOR pulls,
a reviewed allowance declared in `image-licenses.json` alongside MinIO. The interface is
`VEXA_SEARCH_URL` + `VEXA_SEARCH_DIALECT` (`searxng` | `brave`), and a third dialect is one ~20-line
function in `_DIALECTS`: take a client, a URL, a query and a count, return
`[{"title","url","snippet"}]`.

`WebFetch` carries the guard search does not need — a URL the MODEL chose is an outbound destination
picked by a non-operator — so it refuses loopback / link-local / private / reserved targets and
re-checks every redirect hop, exempting only the operator's own `VEXA_SEARCH_URL` host. The rule is
`control_plane/model_endpoint.py`'s, **re-stated rather than imported**: the worker image ships
`worker/`, `llm/`, `shared/` and `contracts/` and deliberately not `control_plane/`, so an import
would be an ImportError in the only process that runs this code.

Raw `httpx`, no vendor SDKs — the protocols are ~10 lines each and a pinned SDK is a heavier
supply-chain surface than the dialect itself.

### Background jobs — [`JOBS.md`](JOBS.md)

A long act (Create, Extend, anything the model hands to `spawn_job`) runs as a **job**: the turn
returns one line at once, the job runs on its own thread with its own harness session and step
count, and its result arrives later as a line plus a refreshed page. The runner is the worker's
(`worker/jobs.py`) and sits ABOVE the harness, so every runner gets it. The only per-runner part is
the `spawn_job` TOOL — a builtin here on `openai-agent`, absent on `claude-code` (whose native
subagent runs inside the turn, which is the behaviour the contract exists to remove). `JOBS.md`
carries the event vocabulary and the rest.

## Configuration

| Env var | Meaning | Default |
|---|---|---|
| `VEXA_RUNNER` | harness adapter key: `claude-code` \| `codex` \| `openai-agent` | `claude-code` |
| `VEXA_LLM_BASE_URL` | openai-agent endpoint | **required** for `openai-agent` (falls back to `ANTHROPIC_BASE_URL`) |
| `VEXA_LLM_API_KEY` | openai-agent credential (optional for local runtimes) | falls back `ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY` |
| `VEXA_LLM_MODEL` | openai-agent fallback model (free string), read only when no `VEXA_AGENT_MODEL` reaches the worker | `VEXA_AGENT_MODEL`; neither → fail-loud at the first request |
| `VEXA_LLM_EXTRA_BODY` | JSON object merged into EVERY openai-agent request | `{}` |
| `VEXA_AGENT_MAX_TOOL_CALLS` / `VEXA_AGENT_MAX_TURN_SEC` | openai-agent per-turn budget | 40 / 900 |
| `VEXA_AGENT_CONTEXT_TOKENS` | openai-agent context ceiling (trims oldest tool results first) | 24000 |
| `VEXA_AGENT_STREAM` | openai-agent SSE streaming (`0` = one blocking request) | `1` |
| `VEXA_SEARCH_URL` | operator-supplied search endpoint for `WebSearch` | empty → `WebSearch` is not attached |
| `VEXA_SEARCH_DIALECT` | wire format of that endpoint: `searxng` \| `brave` | `searxng` |
| `VEXA_SEARCH_API_KEY` | credential for that endpoint (brave needs one; searxng does not) | empty |
| `ANTHROPIC_*`, `HOST_CLAUDE_CREDENTIALS` | claude-code adapter; openai-agent reads `ANTHROPIC_BASE_URL` / `_AUTH_TOKEN` / `_API_KEY` only as the fallbacks above | — |
| `HOST_CODEX_CREDENTIALS`, `OPENAI_API_KEY` | codex adapter subscription-file / API-key auth | — |

**Whose endpoint, the deployment's or a subject's own, is decided by the dispatch, once.** The
`VEXA_LLM_*` and `ANTHROPIC_*` values above are the deployment's: agent-api backfills the
`ANTHROPIC_*` ones and the runtime forwards the rest into every worker. When a subject's Settings →
Models `mode: custom` endpoint passes the operator gate (`VEXA_MODEL_BASE_URL_ALLOW`),
`control_plane.dispatch.subject_route_env` stamps the whole route for that worker: the subject's
endpoint as `ANTHROPIC_BASE_URL` and `VEXA_LLM_BASE_URL`, the subject's key (empty when they set
none) as `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` and `VEXA_LLM_API_KEY`, the subject's
`extra_body` (or empty) as `VEXA_LLM_EXTRA_BODY`, and an empty `CLAUDE_CODE_OAUTH_TOKEN` and
`VEXA_LLM_MODEL`. The model is `VEXA_AGENT_MODEL`, under `VEXA_MODEL_ALLOWLIST`. The runtime never
overrides a key the dispatch stamped, the empty string included. So both harnesses run on the
subject's endpoint, key, model and extra body whatever the deployment's `VEXA_LLM_*` say, the
subject's key reaches no other endpoint, and no deployment credential reaches the subject's. With
no subject endpoint, or a refused one, nothing is stamped and the table above applies unchanged.

The subject's route also carries `VEXA_MODEL_ROUTE=subject`, and the claude-code adapter then
removes any credential stored in the CLI's config directory (`$HOME/.claude/.credentials.json`, where
a process-backend runtime stages the deployment's subscription) before the CLI starts — refusing the
turn if it cannot. A claude CLI with no key of its own signs in from that file, so on claude-code a
subject's endpoint also needs the subject's own key: without one the dispatch refuses the endpoint
(`model_endpoint.route_refusal`, which the Test button asks too) and the deployment route applies.
openai-agent reads no file and sends no credential to a keyless endpoint.

## Rules

- **This module imports NOTHING from product code** (`shared/`, `contracts`, `worker/`,
  `control_plane/`) — it must stay liftable into a standalone brick.
- Vendor names appear only in adapter files (`claude_code.py`, `claude_skills.py`, `codex.py`), never in
  `ports.py`/`registry.py` beyond registry keys.
- UnitEvent shapes (`message-delta` / `tool-call` / `tool-result` / `done{reply,sessionId,ok}` /
  `commit` and the `model-error` / `auth-error` builders in `errors.py`) are FROZEN — the terminal
  reducer and SSE relay consume them field-for-field. They describe the AGENT harness; a meeting's
  feed carries the transcript and nothing else.
- **A provider failure ends the turn TYPED** (P18): `faults.py` is the one model-provider fault —
  `ProviderFault{source: "model-provider", kind, provider, model, status, detail, remedy}`, `kind` one
  of `unpaid` (402) · `unauthorized` (401/403) · `rate_limited` (429) · `unavailable` (5xx, timeout)
  · `refused` (other 4xx). Every harness and provider adapter builds it with `faults.classify` and puts
  it on the failed `done` as `fault` (additive); import it, never define a second one. A first
  `done` that carries a `fault` is never "healed" as a stale resume — the provider refused the turn,
  not the session.
- Session ids are OPAQUE per-harness tokens; an alien/stale id must yield `done.ok=False` (the
  engine's stale-resume retry heals it).
- **Every harness CLI starts as the tools user** (`ports.harness_identity_kwargs`, user `vexa-tools`
  in the worker image) wherever the worker runs as root, so the model's tools cannot read the
  worker's environment through /proc or write its code; the worker hands that user, by group, only
  the workspaces a turn may write and the harness's writable state (`grant_tools_access`) — never a
  repository's `.git`, which stays the worker's (a `.git` an earlier grant opened is closed again).
  The staged skills and the CLI's user scope (`~/.vexa-skills`, `~/.claude`) it may only read
  (`show_tools`), so a turn cannot change what a later turn loads.
  A directory holding a `.git` is made sticky, so the tools user cannot rename a `.git` it does not
  own and put another directory in its place between git's check and git's read.
  A worker that is not root does not switch and is non-dumpable instead (`harden_worker_process`).
  A new adapter launches its CLI with those keyword arguments and `harness_subprocess_env()`.
- **The write-back's git runs through `gitexec`** (`ports._git` → `llm/gitexec.py`, a verbatim copy
  of `shared/gitexec.py`): no hook, fsmonitor, driver or helper a workspace repository names runs
  in the worker.

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
   `agent-api`. The runtime bind-mounts only that file at `$CODEX_HOME/auth.json:ro` in each worker
   and names `CODEX_HOME` (`/tmp/.codex`, `runtime_kernel.workload_env.WORKER_CODEX_HOME`); the
   adapter and the Codex CLI read the same variable. On Kubernetes mount the Secret there
   (`RUNTIME_K8S_SECRET_MOUNTS` with `mountPath: /tmp/.codex/auth.json`, `file: auth.json`).

The adapter keeps rollout history under the private continuity mount's already-ignored
`.claude/codex/sessions/`; the subscription auth file stays in `$CODEX_HOME` and is never copied,
staged, emitted, or returned through the workspace API. `VEXA_CODEX_MODEL` is optional; leaving it
empty uses the subscription account's Codex default and deliberately ignores an inherited
`claude-*` model pin.
