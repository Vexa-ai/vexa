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

**The rule:** the two domains never reach into each other's internals. They meet only through **published
contracts** — the gateway's `api.v1` HTTP surface, or a `.v1`-governed bus carrier (`transcript.v1`,
`tool.v1`). Composition that spans both domains lives **above** them in the cookbook layer — never folded
into either domain.

**Legal acquisition, not "never touch."** The boundary is about *write-ownership* and *how data is
acquired*, not about forbidding possession. The agent **may hold, compose, and serve meeting data
downstream** once it has acquired it **legally** through a published contract (e.g. reading `/transcripts`
via the gateway, or the `transcript.v1` carrier). What's forbidden is **owning/writing** another domain's
carrier, **re-deriving** a producer's data into a competing copy (P23), or reaching into **internals**
(P3). So the agent's live-view composition and its chat-grounding tool are both fine — each legally
acquires the transcript, then uses its own downstream copy.

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
- chat grounded in a live meeting — agent-api folds the meeting's live transcript from its redis Stream
  (`tc:meeting:{native}`, the same wire the copilot tails) into the prompt, not a file (cookbook entry #1)
- live view — the gateway *composes* the meetings transcript feed + the agent card feed
  (`unit:agent-meet-*:out`) into one client stream; neither domain merges the other's data

## 3. Chat grounding — fold the live transcript stream, not a file

When the terminal's `active` tab is a meeting, agent-api grounds the chat turn by reading the meeting's
live transcript directly from its redis Stream `tc:meeting:{native}` — the SAME wire the live copilot
tails (`worker/meeting.py`) and the terminal renders — and folding the segments (refining drafts upserted
by `segment_id`, arrival order preserved, bounded) into the prompt. This happens **fresh on every turn**,
so a follow-up re-reads the latest lines. The transcript stays inside the trusted control plane and rides
the prompt to the isolated worker: no notes-file dependency, no cross-domain HTTP, and no user key or
scoped token in the worker (P15). The copilot still writes a durable `kg/entities/meeting/{native}.md` for
the *finished* record, but live chat no longer depends on it.

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
| **CP6** | chat `active={meeting}` → agent-api folds `tc:meeting:{native}` into the prompt | agent (reads meetings' transcript Stream) | seeded stream → folded `speaker: text` prompt grounding; refining drafts deduped, empty stream → "no transcript yet" |
| **CP7** | status change → `u:{user}:meetings` → gateway fan-in → client | meetings | one status change → exact user-channel frame |
| **CP8** | cookbook "agent-on-meeting" = `POST /bots` + `POST /api/meeting/process` | wiring | meeting input → both calls (right args) + combined state + partial-failure surfaced |

## 5. Cookbook — patterns, not yet a home

We build the first two concrete entries (#1 context grounding, #2 composition) before deciding where the
cookbook layer permanently lives (gateway-composed vs a thin orchestration surface). The patterns to
extract once both exist:
- **Composition over contracts** — a high-level op calls ≥2 domain APIs, owns partial-failure, returns
  combined state; lives above the domains.
- **Per-turn context grounding** — when the turn's `active` context warrants, the trusted control plane
  reads the one in-focus resource (here the meeting's transcript Stream) and folds it into the prompt,
  fresh each turn — keeping the credential/data inside the control plane, never in the isolated worker.

## 6. Deferred (seam wired, implementation follows — P16)

These are intentionally staged: the contract/seam is in place and tested; the runtime piece follows.
- **Gateway-composed live view** (CP5) — today agent-api's SSE *reads* the meetings-owned transcript
  carrier and *composes* it with the agent's cards (a reader composing — no P23 violation). Relocating that
  compose to the gateway (transcript from meetings, cards from agent) is a user-invisible follow-up; the
  merge + gapless cursor are already pinned by the CP5 tests.
- **Cookbook home** — the two concrete entries (#1 tool-authorization, #2 composition) exist; where the
  cookbook layer *permanently* lives (gateway vs a thin orchestration surface) is decided from these real
  instances, not up front.
