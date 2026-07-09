# Terminal — backend: what's needed, what Vexa has, what's missing

> Grounded in a code-level inventory of the current `0.12` branch (`core/`) and the
> `feature/ei-workspace-0.11` branch (`services/`). The headline: **most of the hard backend already
> exists — but it's split across two codebases.** `0.12` is a clean, self-hostable *control plane*;
> the rich agentic/workspace/calendar stack lives on `ei-workspace`. The main job is **consolidation
> (port `ei-workspace` → `0.12`)** plus a focused set of **genuinely new services**.

## The two-codebase reality

- **`0.12` (`core/`)** — minimal viable control plane, clean reorg, self-hostable via `deploy/compose`:
  gateway · meeting-api (bots, transcripts, recordings) · admin-api (users/tokens/webhooks) · runtime
  (workload spawn) · live transcription. The sealed `api.v1` contract *also defines* `/api/chat`,
  `/api/sessions`, `/calendar/*`, `/mcp` — but those are **contract-only stubs in 0.12** (no routes).
  `core/agent/services/agent-api` is a **skeleton**: contract validation only, no HTTP server, no
  Dockerfile, no LLM, not in compose.
- **`ei-workspace` (0.11, `services/`)** — the full Enterprise-Intelligence stack, working end-to-end:
  a real `agent-api` (FastAPI SSE chat, per-org containers, workspace git/S3 sync, knowledge-graph
  lineage), `calendar-service` (Google), the `mcp` server, and a `runtime-api` scheduler.

**Plan: make 0.12 the home.** Port the `ei-workspace` services into `core/` (filling the contract
stubs the gateway already exposes), then build the missing pieces. Nothing below requires throwing
work away — it's port + extend.

## Capability map

Legend — **Have/0.12**: production in 0.12. **Port**: exists on `ei-workspace`, move into 0.12.
**Build**: net-new.

| # | Capability | Status | Where it lives / evidence | Work to do |
|---|---|---|---|---|
| 1 | **Live meeting capture** (bot joins, records, live transcript over WS) | **Have/0.12** | `core/meetings/services/meeting-api`, `core/meetings/services/bot`, gateway `ws.v1` multiplex (`tc:meeting:*`) | None — works. Transcription endpoint is env-configurable (BYO STT). |
| 2 | **Agentic chat runtime** (SSE chat, tools, per-org container, sessions) | **Port** | `ei-workspace:services/agent-api` (full FastAPI). 0.12 has only a contract-validation skeleton + `/api/chat`,`/api/sessions` defined-but-unimplemented in the gateway | Port the FastAPI app + container mgmt into `core/agent`; wire gateway `/api/chat*` routes; ship `vexaai/vexa-agent` image + a compose service. |
| 3 | **Workspace / knowledge graph** (git-backed entities, wikilinks, proposals/lineage) | **Port** | `ei-workspace:services/agent-api/lineage.py` + `workspace-seed/` (people/companies/meetings, dated confidence-scored appends, sign/reject). 0.12 has `workspace.v1` contract but **no persistence adapter** | Port the git+S3 `WorkspacePort` adapter and the lineage proposal pipeline. |
| 4 | **Real-time in-meeting intelligence** (live entity extraction, proactive cards, quick actions) | **Build** | Today lineage runs **post-meeting** (`meeting.completed` webhook) only. No live consumer of the transcript stream. | New: a live extractor that consumes the `ws.v1` transcript stream → entities/actions → pushes proactive cards; the meeting↔workspace live write path. **This is the product's differentiator and the biggest new build.** |
| 5 | **Calendar** (connect, events, meetings=past events) | **Port** | `ei-workspace:services/calendar-service` (Google OAuth, 5-min sync, auto-join, `CalendarEvent` ↔ bot). 0.12: `/calendar/*` contract-only | Port calendar-service into 0.12; wire gateway routes. **Add Outlook/Microsoft Graph** (Build). |
| 6 | **Email / Inbox** (connect mailbox, ingest, importance, propose actions) | **Build** | Nothing anywhere (no IMAP/Gmail/Graph). `user.email` is identity-only. | New `email-service`: Gmail + Microsoft Graph OAuth, message/thread store, `email.received` events, send via agent. Feeds the `Inbox triage` routine. |
| 7 | **Tasks** (model, CRUD, sources, due) | **Build** | No task entity anywhere. | New `tasks` surface: model (title, due, priority, status, source, assignee), CRUD endpoints, agent tool to create/complete from meetings/email/routines. |
| 8 | **Routines = trigger → plan** (time + event triggers, create-from-chat) | **Partial → Build** | `ei-workspace:services/runtime-api/scheduler*` has a **one-shot** scheduler (Redis sorted-set executor, retry); `metadata.cron` is **accepted but ignored**. Only trigger today: `meeting.completed`. | Build: (a) cron execution (`croniter`) on the existing scheduler; (b) a **generic event dispatcher** for `email.received`, `calendar.event_created`, `news.found`, `time.daily`, … → run an agent plan; (c) a routine model (name, trigger, plan) + CRUD + `/routine` create-from-chat. The infra exists; the engine is the build. |
| 9 | **MCP** (tools server) | **Port** | `ei-workspace:services/mcp` wired into the gateway; 0.12 `/mcp` contract-only | Port; optionally expose knowledge-graph resources as MCP tools. |
| 10 | **News ingestion** (scan tracked entities → workspace) | **Build** | Nothing. | New (lower priority): RSS/web fetch tool + `news.found` event → routine. |
| 11 | **Self-host / air-gap** | **Have/0.12 (partial)** | `deploy/compose` stands up gateway+meeting-api+admin-api+runtime+postgres+redis+minio. Air-gappable. | Add agent-api / calendar / mcp / email services to compose; publish the `vexa-agent` image. |
| 12 | **BYO inference** (point the agent LLM at your endpoint) | **Build** | Transcription endpoint **is** configurable (air-gappable). The **agent LLM is a TODO seam** in 0.12 (`core/agent/.../core.py`); `ei-workspace` agent runs a `claude` CLI with a configurable `DEFAULT_MODEL` but no first-class "BYO endpoint" knob. | Build a clean inference adapter: `AGENT_LLM_BASE_URL` / `MODEL` / `API_KEY` so an enterprise can target self-hosted vLLM, in-tenant Azure OpenAI, Bedrock, or on-prem GPUs. |
| 13 | **Enterprise identity (SSO / SCIM)** | **Build** | admin-api = API-token auth only. The dashboard does NextAuth (Google/Azure) **client-side**; the backend has no OAuth/SAML/SCIM. | Build backend OIDC/SAML (Okta, Entra ID, Ping) + SCIM provisioning — required for the enterprise self-host story. |

## Self-host · air-gap · BYO-inference — the specifics

The enterprise (DTCC/MS/Citi) promise is "everything in your infrastructure, no egress, your inference." Status:

- ✅ **Stack is self-hostable** — the whole control plane runs from `deploy/compose` (Postgres, Redis, MinIO, no required SaaS). Air-gappable today for the meeting/recording half.
- ✅ **BYO transcription** — `TRANSCRIPTION_SERVICE_URL`/`_TOKEN` already point at your STT (self-hosted Whisper, in-VPC Azure, etc.).
- ⚠️ **BYO agent inference** — the seam exists but isn't a first-class config. **Must add** `AGENT_LLM_*` envs + adapter so no token ever leaves the network. This is the #1 air-gap blocker.
- ⚠️ **Images** — `vexa-bot` is published; **`vexa-agent` is not yet** — needed for the runtime to spawn the agent. Publish (or build in-cluster) it.
- ❌ **SSO/SCIM** — not at the backend. Required for enterprise.
- 🔎 **Egress audit** — before claiming "no egress," grep the agent/services for any hardcoded external calls (LLM, telemetry, news) and gate them behind config.

## Suggested build order

1. **Port the agentic core into 0.12** (caps 2, 3, 9): agent-api FastAPI + workspace git/S3 + lineage + MCP → fill the gateway's contract stubs; add to compose; publish `vexa-agent`. *Unlocks Chat + Workspace for real.*
2. **BYO-inference adapter** (cap 12) — do this with the port; it's the air-gap gate.
3. **Routines engine** (cap 8): cron on the existing scheduler + a generic event dispatcher + routine CRUD + `/routine`. *Unlocks Routines and is the substrate for everything event-driven.*
4. **Tasks** (cap 7) — small service + agent tool; threads through meetings/email/routines.
5. **Email** (cap 6) → drives `Inbox triage` + the proposed-actions-on-message UX.
6. **Calendar port + Outlook** (cap 5).
7. **Real-time in-meeting intelligence** (cap 4) — the differentiator: live extractor over the transcript stream → proactive cards + live workspace writes.
8. **SSO/SCIM** (cap 13) — gate enterprise self-host GA.
9. **News** (cap 10) — last.

### Net-new services to stand up
`email-service` · `tasks` (could fold into admin-api or its own) · `routines-engine` (cron + event
dispatcher; extends `runtime-api`'s scheduler) · a `live-extractor` (transcript stream → entities) ·
inference adapter + SSO/SCIM in `admin-api`. Everything else is **port from `ei-workspace`**.
