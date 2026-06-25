# core/agent — the execution domain (transcript → governed action)

## Purpose
This module is the **execution domain**: it turns a trigger (a chat turn, a fired
schedule, an external event, a live transcript beat) into a **governed agent action**
committed to a user's `workspace.v1` git repo. It owns 8 control-plane contracts and houses
the `agent-api` service — the one Dispatcher that funnels every trigger through a single
`unit.v1` envelope and spawns an isolated `runtime.v1` agent worker (claude-in-container).
Agents never run in the control plane; isolation *is* the enforcement of governance.

## Seams
| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| consumes | meetings | `meetings/contracts/transcript.v1` (redis stream `transcription_segments` / `tc:meeting:<id>`) | transcript beats → live cards + `session_end` |
| consumes | gateway / terminal | `POST /api/chat`, `POST /events`, `POST /invocations`, `POST /api/routines` | chat turns, external events, routine authoring |
| consumes | identity | `identity/contracts/identity.v1` (`IdentityPort.mint`) | per-dispatch signed token, `canAccess` |
| spawns-over | runtime | `runtime/contracts/runtime.v1` (profile `agent`) | worker `env`: mounted workspaces, token, redis topics, `start` |
| produces | workspace | `agent/contracts/workspace.v1` (git repo) | typed `kg/entities/*` with `EntityFrontmatter` |
| publishes | gateway / surfaces | `gateway/contracts/ws.v1` (redis `unit:<id>:out`, mode `card`) | turn events + `proactive-card.v1` outputs |
| calls | scheduler | `schedule.v1` (a `routine.v1` `kind:scheduled` compiles to a cron job) | a `unit.v1` Invocation as the cron body |

## Contracts
**Owns:** [`unit.v1`](contracts/unit.v1) · [`routine.v1`](contracts/routine.v1) ·
[`workspace.v1`](contracts/workspace.v1) · [`event.v1`](contracts/event.v1) ·
[`tool.v1`](contracts/tool.v1) · [`task.v1`](contracts/task.v1) ·
[`invoke.v1`](contracts/invoke.v1) · [`proactive-card.v1`](contracts/proactive-card.v1).
Only `invoke.v1` + `workspace.v1` are pinned in `contracts.seal.json`; the rest are
**UNSEALED** (sealed per-MVP). **Consumes:** `meetings/contracts/transcript.v1`,
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
- ✅ delivered — live in-meeting copilot: transcript stream → propose-only beats → `proactive-card.v1`
- 🟡 partial — most owned contracts UNSEALED (sealed per-MVP); `invoke.v1` retired once the meetings path migrates
- ⬜ planned — `routine.v1` gains a `target` (agent|meeting) so a routine can schedule a bot
- ⬜ planned — `workspace.v1` meeting-entity convention (a meeting becomes `kg/entities/meeting/*`)
- ⬜ planned — a `session_end` governed write-turn that authors the meeting entity
