# Terminal — backend foundation + the sealed-contract set to establish before MVP0

The *agent runtime unit* as a first-class primitive, the contracts to design/seal **once** before any
MVP, and the dispatcher / output-bus / tool-cred / workspace-data-model designs that make MVP stages
purely additive. Grounded in the real 0.12 seams. Companion to [`ARCHITECTURE.md`](ARCHITECTURE.md)
(frontend) and the MVP plan.

## The one primitive, mapped to what exists
The **unit** = an agent with (1) a per-person git workspace, (2) a CLI/toolbelt scoped by brokered
creds, (3) a plan. Chat / routine / worker / live-in-meeting are the **same unit**, differing only by
**trigger**, **context**, **lifecycle** (warm vs one-shot). 0.12 has ~80% of the substrate:

| Unit facet | Exists | Foundation work |
|---|---|---|
| spawn / warm / one-shot / idle-reap / quota | `runtime.v1` `WorkloadSpec` (`idleTimeoutSec`/`maxLifetimeSec`), kernel `Runtime`, `Enforcer`, per-owner quota (`VEXA_OWNER`) | none to the kernel — the unit *is* a `runtime.v1` workload under profile `agent`; add `unit` profile defaults |
| the unit loop (decide → re-validate → commit) | `core.run()` (`agent_api/core.py`) | swap `DeterministicDecider` for the **claude-CLI `AgentDecisionPort` adapter**; keep `workspace.v1` re-validation verbatim |
| trigger → invocation → spawn | `bridge.py` + `runtime_kernel/scheduler.py` (working croniter) | **generalize** both into one dispatcher over four trigger sources |
| per-person workspace + brokered push | `RealGitWorkspace`, `GitHubVcs` (brokered, reveal-only), `/user/workspace-git`, identity `SecretsPort` | extend to org-trunk/merge; formalize cred storage |
| the LLM seam | `AgentDecisionPort` (TODO) | **the** place the claude-in-container adapter lands |
| output to surfaces | `ws.v1` (transcript/status/chat over Redis), `api.v1` declares `/api/chat`+`/api/sessions` | add a **per-unit topic** + `unit_message` frame |
| tool/cred governance | identity `SecretsPort`, `/mcp` route | the `tool.v1` descriptor + 3-layer enforcement |

## Unit lifecycle (one state machine, two profiles)
`spawn → acquire-context → turn(s) → persist/commit → [warm? sleep : stop]`. **one-shot**
(`meeting.completed`, scheduled routines, event reactions): short `maxLifetimeSec`. **warm** (chat,
live in-meeting): container stays up, `--resume <session>` via `.claude/.session`, `idleTimeoutSec` lets
the `Enforcer` reap idle. Both map onto `runtime.v1` with **no schema change** — the unit is a profile +
env convention over the sealed kernel (P11). `VEXA_OWNER` = the person's identity `subject` → quota per
person.

## agent-api service shape (the FastAPI front door — new)
agent-api today is a library (no HTTP). Foundation adds an entrypoint mirroring
`runtime_kernel/api.py`'s `create_app`:
```
core/agent/services/agent-api/src/agent_api/
  api.py              NEW — FastAPI: POST /api/chat (SSE), DELETE /api/chat, POST /api/chat/reset,
                        GET/POST/PUT/DELETE /api/sessions*, POST /invocations (dispatcher sink),
                        POST /runtime/callback (runtime.v1 RuntimeEvent sink), GET /health
  dispatch.py         NEW — the unit dispatcher (§ dispatcher)
  decision_claude.py  NEW — AgentDecisionPort adapter: claude-in-container (quorum pattern)
  bridge.py           KEEP — folds into dispatch.py as the `transcription` source
  core.py             KEEP — run() stays the unit loop; decider swapped at the composition root
  ports.py            + ToolboxPort, OutputBusPort
```
Carve rationale (P10): agent-api is already a separate service (Python LLM tooling, ADR-0009); the
front door is just its entrypoint. The unit *worker* stays a `runtime.v1` workload (P7). The dispatcher
is a **module** inside agent-api, not a new service.

## The claude-in-container `AgentDecisionPort` adapter (fills the TODO)
The proven `bbb:~/dev/quorum` pattern: the `agent` profile image carries `@anthropic-ai/claude-code`;
**Claude credentials bind-mounted read-only** from the deployment `~/.claude/.credentials.json` (never
baked into the image, never in env, never in the workspace — P15); invoked headless `claude -p
"<prompt>" --allowedTools <scoped> --resume <session>` via `docker exec`; JSON → SSE. The credential
mount is a **profile policy** in `profiles.py`, not a contract field (the kernel never sees
"credentials" — P11). Enterprise/air-gap swaps the bind-mount for BYO inference behind the same port.

## 3-layer enforcement (design now)
AND-ed: (1) **`--allowedTools` scope** — the adapter passes only the tools the unit's `tool.v1`
descriptors permit (capability layer); (2) **sandbox / no-egress** — the `runtime.v1` workload runs with
no outbound network except brokered endpoints (containment layer; a backend/profile policy — critical
for MNPI/air-gap); (3) **post-write `git diff` re-validation** — `core.run()` validates every staged
entity's frontmatter against `workspace.v1` and rejects the commit on any non-conformant file (contract
layer — the one a model can't talk past, P8).

## The dispatcher (generalize scheduler + bridge)
A module `dispatch.py` in agent-api that *reuses* (not forks) `runtime_kernel/scheduler.py` (cron sealed
under `schedule.v1` — croniter, retry/backoff, idempotency). Four trigger sources → one output
(`unit.v1` Invocation → `runtime.v1` spawn):

| Trigger | Source (0.12 seam) | Mechanism |
|---|---|---|
| `message` | `POST /api/chat` | direct → resume/spawn warm chat unit |
| `scheduled` | a `schedule.v1` cron job whose `request.url` = agent-api `POST /invocations`, body = a `unit.v1` Invocation | scheduler already POSTs on cron — **no `schedule.v1` change** |
| `event` | a Redis stream `evt:*` (email/calendar/task event-sources publish) | a poll loop (mirror `bridge.run_once`) → Invocation |
| `transcription` | `tc:meeting:{id}:mutable` (live) + `transcript.v1 session_end` (completed) | today's `bridge.py` folds in unchanged; live path subscribes the mutable stream |

A routine is literally a `schedule.v1` cron job whose body is a `unit.v1` Invocation. `routine.v1` is a
higher-level authoring entity that *compiles down* to that.

## THE CONTRACT SET TO DESIGN ONCE (ordered, before MVP0)
Discipline: a **new** contract = new `.vN` dir + `*.schema.json` + `golden/` + `validate.mjs` +
`README.md`, left **unsealed** until an MVP seals it (`pnpm seal:contracts` on a `lane:contract` PR). A
**back-compat change to a sealed** contract = edit + re-seal on `lane:contract`. **Breaking** = new
`vN+1`. Goldens are the spec (P8). Freeze the envelope every stage depends on first, then the entities
that ride it, then touch existing sealed contracts last and minimally.

### `unit.v1` — NEW, the one to get right once *(seal in foundation)*
**Decision: a new `core/agent/contracts/unit.v1`, NOT an evolution of the sealed `invoke.v1`.**
`invoke.v1` requires `meeting` and is titled "meeting → agent trigger"; three of four triggers have no
meeting. Broadening it is a breaking reshape; a new `unit.v1` is the honest move — `invoke.v1` stays
valid for the meetings path (consumers unaffected) and `unit.v1` subsumes it going forward (`bridge.py`
keeps emitting `invoke.v1`; `dispatch.py` emits `unit.v1`; `/invocations` accepts both during
transition; `invoke.v1` retired later — a future breaking step, not foundation).
```jsonc
{ "trigger": "message | scheduled | event | transcription",     // taxonomy frozen NOW
  "context": { "kind": "none | meeting | email | generic",
               "meeting": {"meeting_id":"...","session_uid":"...","platform":"..."},  // kind=meeting
               "ref": {"uri":"...","etag":"..."} },                                   // kind=email|generic
  "subject": "user-id",                 // identity.v1 subject = the PERSON = quota owner (VEXA_OWNER)
  "workspace_repo": "https://...",      // per-person repo (from invoke.v1 verbatim)
  "workspace_ref": "main",
  "plan": { "ref": "plan/<name>.md", "prompt": "..." },
  "lifecycle": "oneshot | warm",        // → runtime.v1 idle/maxlife mapping
  "output": { "topic": "unit:<id>:out", "modes": ["sse","card","notification"] },
  "tools": ["tool-name"] }              // OPTIONAL — the scoped toolbelt
```
All four `trigger` values + every optional field present from day one, so MVPs **populate**, never
re-cut. `context.kind:"none"` is the chat default; email/calendar/tasks ride as `kind:"email"|"generic"`
with an opaque `ref` — tools + event-sources, NOT domains (no `calendar.v1`/`email.v1` platform
contract). `subject` keys quota/cred brokerage on the person. Seal: golden per `trigger × context.kind`
+ validate + README → **seal in the foundation**. Internal: widen `AgentDecisionPort.decide(payload)` →
`decide(context)` (a `models.py` change, no published-contract impact); `core.run()` re-validation
unchanged.

### Stubbed now (unsealed), sealed in their MVP
- **`routine.v1`** `{id, owner, name, trigger:{kind:scheduled|event, cron?, event?}, plan:{ref}, lifecycle, enabled}` — compiles to a `schedule.v1` job (body = `unit.v1` Invocation).
- **`task.v1`** `{id, owner, title, state:open|doing|done|blocked, plan_ref?, due?, links:[entity-uri]}` — lives as a `kg/entities/task/<slug>.md` (workspace.v1) **and** has a wire shape for the event bus.
- **`tool.v1`** (the cred-governance descriptor) `{name, scope, grant:auto|gate, cred_ref:"secret://...", transport:mcp|builtin, mcp_server?, barriers:["mnpi","info-barrier"]}` — where MNPI/info-barrier/air-gap is *declared*.
- **`proactive-card.v1`** `{id, unit_id, owner, kind:suggestion|alert|digest, title, body_md, actions:[{label, invoke:unit.v1-ref}], ts}` — travels on the unit output bus.

### Updates to EXISTING sealed contracts (foundation phase — additive re-seal on `lane:contract`)
| Contract | Change | Kind |
|---|---|---|
| `gateway/ws.v1` | add a `unit_message` data type `{type:"unit_message", unit_id, topic, payload}` (carries chat-delta / card / notification frames); document the per-unit topic `unit:<id>:out` (forwarded verbatim like `tc:`/`bm:`/`va:`). ws.v1 already allows additive type-tagged data. | evolution (additive) |
| `gateway/api.v1` | implement-to the already-declared `/api/chat`, `/api/chat/reset`, `/api/sessions*`; add chat (SSE) + session req/resp schemas; optional `POST /api/invocations`. `/calendar/*` stays a tool connect/status, not a domain. | evolution (fill declared stubs); `gate:contract-conformance` stays green |
| `identity/identity.v1` | add `Scope` members (e.g. `agent`, `tools`) + `ResourceKind` (`workspace`, `tool_cred`) so `canAccess` guards agent + tool-cred paths (P20). | evolution (additive enum) |
| `agent/invoke.v1` | none — superseded by `unit.v1`, frozen for the meetings path until migrated. | frozen |
| `runtime/runtime.v1`, `schedule.v1` | none — unit is a profile+env convention; routines compile to existing `schedule.v1` jobs. | frozen |

`/user/workspace-git` + `admin-api user.data.workspace_git` = the per-person cred storage (already
present); foundation wires `unit.v1.subject` → that record → `SecretsPort`. No new contract.

## Unit output bus (`ws.v1` topics)
One mechanism, three modes over Redis, forwarded verbatim by the gateway (the `tc:`/`bm:`/`va:` pattern):
per-unit topic `unit:<unit_id>:out`; mode multiplex on the `unit_message` frame's `payload.type`
(`chat_delta` → SSE chat; `card` → `proactive-card.v1`; `notification` → badge/toast). A surface opens
`/ws`, sends `{action:"subscribe", units:[{unit_id}]}`; the gateway authorizes via `canAccess(subject,
ws_subscribe, unit_id)` (P20) and forwards. New surfaces (telegram, desktop, CLI) subscribe by unit
topic with zero core change.

## Tool/cred layer
The cred spine = identity's brokered `SecretsPort` (proven by `GitHubVcs`: redacted `BrokeredSecret`,
`reveal()` only at use, never logged, audited). Every tool cred is a `secret://...` reference in
`tool.v1.cred_ref` — never a value. `grant:"auto"` tools enter `--allowedTools`; `grant:"gate"` tools
require per-call human approval surfaced as a `proactive-card.v1` action. **MCP is the tool-attachment
mechanism** (gateway already proxies `/mcp`); a `tool.v1` with `transport:"mcp"` attaches an MCP server
to the unit iff its scope is in the allow-set. Governance is enforced at the tool+cred layer:
`tool.v1.barriers` + `canAccess` (default-deny) decide attachment; the **sandbox/no-egress** layer makes
air-gap real (a unit handling MNPI runs with no outbound network except brokered, barrier-cleared
endpoints); the brokered cred means the unit never holds a raw key.

## Per-person workspace + org-trunk-merge data model (fix the topology now)
```
per-person repo:  <org>/vexa-ws-<person>.git   branch main = the person's trunk (units commit+push here)
org-trunk repo:   <org>/vexa-ws-org.git        branch main = shared org knowledge
merge (git-like-a-software-project):
  - a unit writes to its PERSON repo only (least privilege; one person = one repo = one quota owner);
  - a scheduled "triage/merge" ROUTINE periodically fetches each person repo, opens a reviewable merge
    person/main → org/main, and triages conflicts like a software team merges trunk;
  - entities carry stable workspace.v1 `id` so cross-repo entities reconcile by id, not path.
```
Why now: the person-repo-vs-org-trunk split dictates `unit.v1.workspace_repo` (always the *person* repo),
the quota axis (person), the cred scope (`repo:push` to one repo), and the triage routine's existence.
No new contract — a convention over `workspace.v1` + a `routine.v1` instance; documented in
`workspace.v1/README.md`.

## Foundation phase — Definition of Done (all before MVP0)
1. `unit.v1` created (goldens: 4 triggers × context kinds) + validate + README — **sealed** (`lane:contract`).
2. `ws.v1` + `api.v1` + `identity.v1` additive edits — **re-sealed** (`lane:contract`); `gate:contract-conformance` green.
3. `routine.v1`, `task.v1`, `tool.v1`, `proactive-card.v1` — created **unsealed** (stubs; validate under `gate:schema`).
4. agent-api `api.py` front door + `dispatch.py` + `decision_claude.py` scaffolded behind ports; `core.run()` re-validation kept; `AgentDecisionPort` widened to `unit.v1` context.
5. Unit lifecycle (warm/oneshot/resume) mapped onto `runtime.v1` profile defaults; `VEXA_OWNER`=subject quota wired.
6. Output bus `unit:<id>:out` + `unit_message` frame proven (a golden round-trip).
7. Person-repo / org-trunk topology documented in `workspace.v1/README.md`.
8. `pnpm gates` green (new contracts sealed-or-unsealed-and-validating).

Each MVP then only **populates optional `unit.v1` fields, seals a stubbed contract, or adds a
`tool.v1`/`routine.v1` instance** — never re-cuts the envelope, the bus, the quota axis, or the repo
topology. That is the test of whether the foundation is right.
