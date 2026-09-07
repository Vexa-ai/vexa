# agent-api — the agent control plane (Python)

## Purpose

The agent domain's HTTP front door and the one place dispatches turn into runtime spawns. It
mirrors the `runtime/` kernel api: it never runs an agent in-process — it builds a `unit.v1`
*now*-dispatch, hands it to the Dispatcher (which spawns the sandboxed worker via `runtime.v1`),
and RELAYS the dispatch's output Stream back as SSE. It carries `/api/chat`, the `/invocations`
sink, `/api/routines`, `/events`, the `/api/meeting/*` live-meeting surface, `/api/workspace/*`
reads, the in-process `transcription_watcher`, and the in-container worker (`serve`).
Python because the agent domain is the LLM/tooling + runtime ecosystem (P13).

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| consumes | gateway / terminal | `POST /invocations` | a `unit.v1` dispatch → one `runtime.v1` agent spawn |
| consumes | gateway / terminal | `POST /api/chat` (SSE), `/api/chat/reset`, `GET /api/sessions` | a chat now-dispatch; SSE view of `unit:<id>:out` |
| consumes | any integration | `POST /events` | an `event.v1` → `unit.v1` (carried plan) → dispatch |
| consumes | terminal | `POST /api/routines`, `GET /api/routines`, `DELETE /api/routines/{id}` | a `routine.v1` → a `schedule.v1` cron job |
| consumes | bridge / terminal | `POST /api/meeting/{bot,stop}`, `GET /api/meetings/live`, `GET /api/meeting/stream` | send/stop the bot; SSE relay of the meeting's transcript stream |
| consumes | terminal | `GET /api/workspace/{tree,file,git}` | workspace tree, file content, git state |
| calls | runtime kernel | `runtime.v1` (Dispatcher → RuntimePort) | the worker container `env` (repo URL + scoped token) |
| calls | gateway / meeting-api | `POST /bots`, `DELETE /bots/{platform}/{native_id}` | forward our self-hosted bot in/out of a meeting |
| consumes | self-hosted bots | redis stream `transcription_segments` | live segments, tailed by `transcription_watcher` |
| reads | terminal | redis stream `tc:meeting:{uid}` | per-meeting transcript wire (drafts + finals); meeting-api's collector is the single writer (P23) |

## Contracts

**Owns:** `core/agent/contracts/unit.v1`, `event.v1`, `invoke.v1`, `routine.v1`, `tool.v1`,
`task.v1`, `proactive-card.v1`, `workspace.v1` (the agent domain's contracts).
**Consumes:** `core/runtime/contracts/runtime.v1` + `schedule.v1`, `core/meetings/contracts/transcript.v1`,
`core/identity/contracts/identity.v1`, `core/gateway/contracts/api.v1`. Schemas are read by path and
jsonschema-validated — never importing neighbour code. All sealed in `contracts.seal.json` (repo root).

## Isolated evaluation

```bash
uv run pytest -q        # uv manages this package's own venv/deps
```

`tests/` spans L1 contract-consumer (`test_contracts_consumer.py`) · L2 unit with faked ports
(`test_api.py`, `test_routines.py`, `test_events.py`, `test_tools.py`, `test_worker.py`,
`test_chat_runner.py`, `test_config.py`) · L3 integration over real git + a real Claude loop
(`test_real_git_workspace.py`, `test_github_vcs.py`, `test_agent_llm_loop.py`).

## Status

- ✅ delivered — `/invocations` dispatch sink → `runtime.v1` spawn
- ✅ delivered — `/api/chat` SSE relay of `unit:<id>:out`, `/api/chat/reset`, `/api/sessions`
- ✅ delivered — `/api/routines` CRUD → `schedule.v1` cron jobs
- ✅ delivered — `/events` generic ingress (`event.v1` → `unit.v1`)
- ✅ delivered — `/api/meeting/{bot,stop,stream}`, `/api/meetings/live` live-meeting surface
- ✅ delivered — `transcription_watcher`: register the live meeting off `transcription_segments`
- ✅ delivered — `/api/workspace/{tree,file,git}` reads
- ✅ delivered — in-container worker (`serve`)
- ✅ delivered — multi-session chat: real conversation threads keyed `agent-{subject}-chat-{session}`
  (default `main`, back-compat), per-thread continuity file (`.claude/sessions/{session}.session`,
  `main` migrates from the legacy `.claude/.session`), and a durable redis-backed session index
  (`/api/sessions` newest-first, `/api/chat` upserts, `/api/chat/reset` drops thread + continuity).
  All threads live in the ONE user workspace (conceptually `type: user`).
- 🗑 removed (PRD decision 34) — the in-product inference pipeline: `/api/meeting/{start,process}`,
  the per-meeting copilot dispatch and its `agents/meeting.md` config, the `proc:meeting:{row}`
  cleaned-notes stream and the `processed-notes.v1` contract, the SSE's merge of the copilot's
  out-stream, and every `VEXA_LLM_*` completion dial. The product runs no model calls of its own
  beside the agent; a meeting reaches the agent over the MCP, on a human's turn.
- ✅ delivered — workspace skills: governed `skills/<name>/SKILL.md` (a VISIBLE, git-tracked tree,
  seeded with one example) symlinked into `.claude/skills` per turn so the isolated worker's `claude`
  auto-discovers them. `agents/` + `skills/` are the two per-workspace agent-extension homes. Skill
  helper scripts run under the turn's existing `--allowedTools` grant (no separate skills gate).
- ⬜ planned — multi-workspace (company/service tiers — a FUTURE axis beyond the single user workspace)
- 🟡 partial — in-memory live-meeting registry (redis-backed adapter pending)
- ⬜ planned — GET /api/meetings (proxy meeting-api + merge live registry)
- ⬜ planned — a session_end doc-binding write turn
- ⬜ planned — WS publishers (u:{user_id}:meetings on registry add/stop/drop, u:{user_id}:workspace on commit)
