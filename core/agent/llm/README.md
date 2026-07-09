# llm — the detached LLM + agent-harness module

Everything vexa knows about model providers and coding-agent CLIs lives HERE, behind two
provider-agnostic ports. Product code (the meeting copilot, chat, routines) imports only the
front door (`llm/__init__.py`) and never names a vendor.

## The two ports (two call shapes)

| Port | Call shape | Used by | Selected by |
|---|---|---|---|
| `CompletionPort` | plain prompt→text HTTP call — no tools, no subprocess | meeting card beats | `VEXA_LLM_PROVIDER` |
| `HarnessPort` | a CLI coding agent over the mounted workspace — tool loop, sessions, streamed UnitEvents | post-meeting doc, chat, routines | `VEXA_RUNNER` |

Both are `typing.Protocol` (duck-typed, mirroring `core/runtime`'s `Backend` port); adapters are
selected env-driven in `registry.py` and constructor-injected everywhere, so tests use trivial
fakes.

## Adapters

- **Completions**: `openai_compat.py` (DEFAULT — OpenRouter, Ollama, vLLM, LM Studio, OpenAI, any
  gateway speaking `POST {base}/chat/completions`) · `anthropic_api.py` (the Messages dialect —
  api.anthropic.com, LiteLLM proxies, DeepSeek/GLM Anthropic-compatible endpoints) ·
  `claude_cli.py` (beats via the claude CLI on mounted SUBSCRIPTION credentials — no API key;
  slower per beat; for subscription-only deployments).
- **Harnesses**: `claude_code.py` (the `claude` CLI — argv build, stream-json parsing, `.claude/`
  continuity + skills wiring, credential preflight). Open-source runners (OpenCode, Aider, Goose)
  slot in as new adapter files + one registry line.

Raw `httpx`, no vendor SDKs — the protocols are ~10 lines each and a pinned SDK is a heavier
supply-chain surface than the dialect itself.

## Configuration

| Env var | Meaning | Default |
|---|---|---|
| `VEXA_LLM_PROVIDER` | completion adapter: `openai-compat` \| `anthropic` | `openai-compat` |
| `VEXA_LLM_BASE_URL` | provider endpoint | anthropic: `https://api.anthropic.com`; openai-compat: **required** (falls back to `ANTHROPIC_BASE_URL`) |
| `VEXA_LLM_API_KEY` | credential (optional for local runtimes) | falls back `ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY` |
| `VEXA_LLM_MODEL` | deployment-default model (free string) | empty → fail-loud at completion call |
| `VEXA_LLM_MAX_TOKENS` | Messages-API max_tokens | 4096 |
| `VEXA_RUNNER` | harness adapter key | `claude-code` |
| `ANTHROPIC_*`, `HOST_CLAUDE_CREDENTIALS` | claude-code adapter ONLY | — |

## Rules

- **This module imports NOTHING from product code** (`shared/`, `contracts`, `worker/`,
  `control_plane/`) — it must stay liftable into a standalone brick.
- Vendor names appear only in adapter files (`claude_code.py`, `anthropic_api.py`), never in
  `ports.py`/`registry.py` beyond registry keys.
- UnitEvent shapes (`message-delta` / `tool-call` / `tool-result` / `done{reply,sessionId,ok}` /
  `commit` and the `model-error` / `auth-error` builders in `errors.py`) are FROZEN — the terminal
  reducer and SSE relay consume them field-for-field.
- Session ids are OPAQUE per-harness tokens; an alien/stale id must yield `done.ok=False` (the
  engine's stale-resume retry heals it).

## Adding a provider / runner

1. New adapter file implementing the port (copy the closest existing one).
2. One line in `registry.py`'s table.
3. Unit test with a fake transport (`httpx.MockTransport`) or fake `exec_fn` — see
   `tests/test_llm_openai_compat.py` / `tests/test_llm_claude_code.py`.
