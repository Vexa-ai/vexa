# Control Plane — the meeting/agent SoC, the terminal jobs, and the critical paths

> Companion to [`ARCHITECTURE.md`](ARCHITECTURE.md). That file is the constitution; this one is the
> applied separation-of-concerns for **live meetings + the agent copilot**, and the catalog of critical
> paths we prove deterministically. Governed by **P2** (couple only through contracts), **P3**
> (`meetings ⊥ agent`), **P23** (one writer per carrier; readers never re-derive).

## 1. The three layers

| Layer | What it is | Owns | Never does |
|---|---|---|---|
| **Domain** | `meetings`, `agent` — each a bounded context with its own carriers/logic | meetings: bots, meeting rows, the transcript (single writer), status. agent: copilot lifecycle, chat, workspace, notes/cards, config | reach into the other domain's internals; re-derive the other's data |
| **API** | each domain's published, gateway-fronted endpoints | meetings-control (`/bots`, `/meetings`, `/transcripts`, `/intent`, `/ws` status). agent-control (`/api/meeting/process`, `/api/chat`, `/api/workspace/*`, `/api/models`) | hold business logic in the gateway; let a client reach a domain backend directly |
| **Top-level wiring (cookbook)** | composed operations + tool-authorization patterns that deliver *state* | "agent listening on a meeting" (compose bot-spawn + copilot-enable); per-turn meeting-scoped tool grants | live *inside* a domain (that re-merges `meetings ⊥ agent`); compose over anything but published contracts |

**The rule:** the two domains never talk to each other directly. They meet **only at the gateway edge**,
over published contracts (`transcript.v1`, `api.v1`, `tool.v1`). Composition that spans both domains lives
**above** them in the cookbook layer — never folded into either domain.

`send bot ≠ start copilot` — two toggles, two domains. The bot (meetings) makes the transcript *flow*; the
copilot (agent, the `proc:on` toggle) *processes* it. The cookbook layer is where a single high-level op
("agent on this meeting") composes the two.

## 2. Terminal jobs → domain map

**Meetings-domain control** (terminal → gateway → meeting-api):
- send / stop / re-send bot — `POST /bots`, `DELETE /bots/{platform}/{native}`
- schedule / set intent + cancel — `PUT /meetings/{platform}/{native}/intent`
- list meetings (live + past) — `GET /meetings`
- transcript history — `GET /transcripts/{platform}/{native}`
- live status for ALL user meetings (left pane) — `WS /ws`, auto-subscribed to `u:{user_id}:meetings`
  at connect; meeting-api publishes every status change there (no polling). *Already built.*

**Agent-domain control** (terminal → gateway → agent-api):
- enable/disable copilot ("start agent listening") — `POST /api/meeting/process` (the `proc:on` toggle)
- chat with copilot — `POST /api/chat`
- read meeting doc/notes — `GET /api/workspace/file` (`kg/entities/meeting/{native}.md`)
- browse workspace — `GET /api/workspace/tree` (the user's workspace git repo = durable agent memory)
- configure copilot — edit workspace `agents/meeting.md` (its body is spliced into the live extraction
  prompt every turn — it *is* the real-time steering prompt)
- model list — `GET /api/models`

**Cross-domain (cookbook / composed at the edge):**
- "agent on meeting" — one op = `POST /bots` + `POST /api/meeting/process` (cookbook entry #2)
- chat grounded in a live meeting — the agent reads the transcript through a **meeting-scoped tool**
  (meeting-api `/transcripts`), not a file (cookbook entry #1)
- live view — the gateway *composes* the meetings transcript feed + the agent card feed
  (`unit:agent-meet-*:out`) into one client stream; neither domain merges the other's data

## 3. Chat grounding — via a tool, not a file

The chat agent learns the live transcript by being **told it is in a meeting** and **granted a per-turn,
meeting-scoped tool** that reads meeting-api `/transcripts/{platform}/{native}`. The copilot still writes a
durable `kg/entities/meeting/{native}.md`, but chat no longer *depends* on that file. This keeps the
transcript a meetings-domain fact, consumed by the agent through the published contract on demand
(P23/P3) — and is the first concrete **tool-authorization** cookbook pattern: a short-lived
(`mint_dispatch_token`, 900s) grant scoped to one `(platform, native_id)`, attached only when the chat's
`active` context is a meeting.

## 4. Critical-path catalog (proven with deterministic fixtures)

Each path: simplest perfect fixture in → frozen expected output, byte-identical across two runs (stubbed
LLM/turn). Where an LLM reply is inherently non-deterministic, assert the *plumbing*, never the prose.

| ID | Path | Owner | Fixture → output |
|---|---|---|---|
| **CP1** | raw `transcription_segments` → collector `ingest()` → `:mutable` + durable hash | meetings | 2 segments (1 confirmed, 1 pending) → exact bundle + stored hash |
| **CP2** | collector single-writer → native transcript feed (D7) | meetings | numeric segments → exact native-keyed, time-anchored entries |
| **CP3** | `proc:on` → watcher arms → worker reads from `:cursor` | agent | flag + 3-seg stream + cursor → dispatch with right `transcript_start_id`; cursor advances |
| **CP4** | `serve_meeting`: segments → gate → stub `card_turn` → notes/cards + doc | agent | 3 segs (speaker change) → exact cards/notes/doc |
| **CP5** | live view = transcript feed + card feed → one client stream, gapless resume by a dual-stream cursor | agent-api SSE today (reader-composes); gateway-composed eventually | canned transcript + cards → merged ordered stream, gapless resume | `_encode/_decode_sse_cursor` + `test_meeting_stream` (`test_api.py`) |
| **CP6** | chat `active={meeting}` → meeting-scoped tool grant → agent calls `/transcripts` | agent↔meetings (contract) | chat body → dispatch context+tool+scoped token; MCP targets `/transcripts/{platform}/{native}` |
| **CP7** | status change → `u:{user}:meetings` → gateway fan-in → client | meetings | one status change → exact user-channel frame |
| **CP8** | cookbook "agent-on-meeting" = `POST /bots` + `POST /api/meeting/process` | wiring | meeting input → both calls (right args) + combined state + partial-failure surfaced |

## 5. Cookbook — patterns, not yet a home

We build the first two concrete entries (#1 tool authorization, #2 composition) before deciding where the
cookbook layer permanently lives (gateway-composed vs a thin orchestration surface). The patterns to
extract once both exist:
- **Composition over contracts** — a high-level op calls ≥2 domain APIs, owns partial-failure, returns
  combined state; lives above the domains.
- **Per-turn scoped tool grant** — attach a tool only when context warrants, scope a short-lived token to
  the exact resource, authorize at the edge.

## 6. Deferred (seam wired, implementation follows — P16)

These are intentionally staged: the contract/seam is in place and tested; the runtime piece follows.
- **The meeting-read MCP server** (cookbook #1) — the `tool.v1` descriptor, the dispatch grant, the scoped
  token mint, and `VEXA_MEETING_NATIVE_ID/PLATFORM` env are wired (CP6). The live MCP server binary that
  calls `/transcripts`, the `VEXA_MEETING_TOKEN` export, and meeting-api's verification of a
  meeting-scoped token are the remaining implementation.
- **Gateway-composed live view** (CP5) — today agent-api's SSE *reads* the meetings-owned transcript
  carrier and *composes* it with the agent's cards (a reader composing — no P23 violation). Relocating that
  compose to the gateway (transcript from meetings, cards from agent) is a user-invisible follow-up; the
  merge + gapless cursor are already pinned by the CP5 tests.
- **Cookbook home** — the two concrete entries (#1 tool-authorization, #2 composition) exist; where the
  cookbook layer *permanently* lives (gateway vs a thin orchestration surface) is decided from these real
  instances, not up front.
