# core/agent — the execution domain (transcript → governed action)

## Purpose
This module is the **execution domain**: it turns a trigger (a chat turn, a fired
schedule, an external event, a live transcript beat) into a **governed agent action**
committed to a user's `workspace.v1` git repo (owned by [`core/workspaces`](../workspaces),
PRD decision 47 — this domain consumes it). It owns 7 control-plane contracts and houses
the `agent-api` service — the one Dispatcher that funnels every trigger through a single
`unit.v1` envelope and spawns an isolated `runtime.v1` agent worker. The worker reaches models
and coding-agent CLIs only through the provider-agnostic [`llm`](llm) module (completion +
harness ports; `claude-code` is the default harness adapter, selected by `VEXA_RUNNER`).
Agents never run in the control plane; isolation *is* the enforcement of governance.

**The workspace model an agent turn sees** — the three tiers (`_global` RO / normal / `_system` RW),
personal + normal single-rank workspaces, the sharing model, and live in-meeting collaboration — is
documented in **[`docs/docs/core/workspaces.mdx`](../../docs/docs/core/workspaces.mdx)**.

## Boundary (SoC)
**This domain is about:** chat, the user's `workspace.v1`, agent turns and agent config. **It is never about:** bot lifecycle, the meeting row, or *owning* the **transcript carrier**.

The agent **may hold, compose, and serve meeting data downstream** — *once it has acquired it legally*,
through a **published contract**: the gateway's `/transcripts` API (e.g. the meeting-scoped read tool) or
the `transcript.v1` carrier on the bus. What is forbidden is **owning/writing** the transcript carrier,
**re-deriving** a producer's data into a competing copy (P23), or reaching into meetings **internals**
(P3). `meetings ⊥ agent`: the two domains never call each other's internals; they meet only through
published contracts (at the gateway, or a `.v1` carrier). Cross-domain composition ("agent on a meeting")
lives in the cookbook layer *above* both domains, never inside this one. See
[`docs/docs/architecture/control-plane.mdx`](../../docs/docs/architecture/control-plane.mdx).

## Seams
| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| consumes | meetings | `meetings/contracts/transcript.v1` (redis stream `transcription_segments` / `tc:meeting:<id>`) | transcript beats → live cards + `session_end` |
| consumes | gateway / terminal | `POST /api/chat`, `POST /api/routines` | a person's chat turns (never a worker's: a delegated identity is refused) and routine authoring |
| consumes | the internal tier · the runtime's scheduler | `POST /events` (internal tier only), `POST /invocations` (internal tier, or a routine job this service signed — `X-Vexa-Dispatch-Signature`, `control_plane/dispatch_sink.py`) | external events and fired routines; the caller is authenticated before the body, which names the person, is read |
| verifies | gateway | `gateway/contracts/gateway-identity.v1` (`X-Vexa-Identity`, `control_plane/identity_token.py`) | the caller's identity, signed by the edge with a key only it holds (agent-api holds the public key); an unsigned `x-user-*` header is believed only from the internal tier (`X-Internal-Secret`) |
| publishes | gateway's MCP service | `mcp.tools.v1.json` at `/.well-known/mcp-tools.json` (routes in `mcp.tools.v1.openapi.json`) | the agent's tools — workspaces and their page verbs, the claim book, membership, the transcript highlight, Connections, the person's clock, `chat_name` — served by the one assembled MCP server to a person's client and to every worker; `tests/test_prompt_tools_served.py` holds every tool a product prompt names to that served set |
| consumes | identity | `identity/contracts/identity.v1` (`IdentityPort.mint`) | per-dispatch signed token, `canAccess` |
| spawns-over | runtime | `runtime/contracts/runtime.v1` (profile `agent`) | worker `env`: mounted workspaces, token, redis topics, `start`, and the toolbelt (`VEXA_MCP_URL` = the gateway's `/mcp`, a per-dispatch delegation token) |
| produces | workspace | `workspaces/contracts/workspace.v1` (git repo) | typed `kg/entities/*` with `EntityFrontmatter` |
| publishes | gateway / surfaces | `gateway/contracts/ws.v1` (redis `unit:<id>:out`, mode `card`) | turn events + `proactive-card.v1` outputs |
| calls | scheduler | `schedule.v1` (a `routine.v1` `kind:scheduled` compiles to a cron job) | a `unit.v1` Invocation as the cron body, signed in the job's request headers |
| calls | credential broker (this domain's own service) | `contracts/credential-broker.v1`, roles `agent` and `git` | Connections requests and results, each agent-role call with the gateway's `X-Vexa-Identity` for the person forwarded unchanged; never a stored credential |

## Contracts
**Owns:** [`unit.v1`](contracts/unit.v1) · [`routine.v1`](contracts/routine.v1) ·
[`event.v1`](contracts/event.v1) ·
[`tool.v1`](contracts/tool.v1) · [`task.v1`](contracts/task.v1) ·
[`invoke.v1`](contracts/invoke.v1) · [`proactive-card.v1`](contracts/proactive-card.v1) ·
[`credential-broker.v1`](contracts/credential-broker.v1).
Only `invoke.v1` is pinned in `contracts.seal.json`; the rest are
**UNSEALED** (sealed per-MVP). **Consumes:** `meetings/contracts/transcript.v1`,
[`workspaces/contracts/workspace.v1`](../workspaces/contracts/workspace.v1) (moved out of this
domain, PRD decision 47 step 1 — still sealed in `contracts.seal.json`, now under its own path),
`runtime/contracts/runtime.v1`, `identity/contracts/identity.v1`, `gateway/contracts/ws.v1`.

## Isolated evaluation
- **Contracts** (L1): each `contracts/*.v1` ships a `validate.mjs`; `gate:schema` checks goldens ≡ schema.
- **Service** `services/agent-api` (L1–L3): `tests/` covers contract-consumer, the LLM loop, dispatch,
  events, routines, tools, bridge, and real git workspace; `eval/replay/` replays transcript fixtures (L4 live).
  ```bash
  cd services/agent-api && uv run pytest -q   # L1 contract · L2 unit (ports faked) · L3 integration
  ```

## Status
- ✅ delivered — one `unit.v1` Dispatcher; every trigger (message/scheduled/event/transcription) → one path
- ✅ delivered — chat dispatch, warm-session resume, workspace git commit (`workspace.v1`)
- ✅ delivered — generic event ingress (`event.v1` → `unit.v1`) and tool mechanism (`tool.v1` → `--allowedTools` + injected MCP)
- ✅ delivered — routines: `routine.v1` `kind:scheduled` compiles to a `schedule.v1` cron job
- 🗑 removed (PRD decision 34) — the live in-meeting copilot (transcript stream → propose-only
  beats → `proactive-card.v1`) and the `processed-notes.v1` stream it produced. The product runs no
  model calls of its own beside the agent; a meeting reaches the agent over the MCP.
- 🟡 partial — most owned contracts UNSEALED (sealed per-MVP); `invoke.v1` retired once the meetings path migrates
- ⬜ planned — `routine.v1` gains a `target` (agent|meeting) so a routine can schedule a bot
- ⬜ planned — `workspace.v1` meeting-entity convention (a meeting becomes `kg/entities/meeting/*`)
- ⬜ planned — a `session_end` governed write-turn that authors the meeting entity
