# Vexa Terminal

**An AI-first knowledge-worker terminal — Claude Code × Outlook, on a backend of meeting bots and an agentic runtime.** Deployable fully self-hosted (your VPC or air-gapped) with BYO inference.

One agent, one persistent chat spine, operating over your whole work surface: live meetings, an office workspace (a git-backed knowledge graph), email, calendar, tasks, and routines. The agent doesn't just answer — it *acts and commits*: it maintains the knowledge graph, files tasks, drafts email, and runs scheduled/triggered routines.

## Why "terminal"

Not a dashboard. It's keyboard-first and command-driven (the composer is a `/`-skill bar), dense, and dark — the Claude-Code feel — fused with the comms surface of Outlook (inbox + calendar). The backend underneath is real: Vexa meeting bots (live transcription) and a per-org agentic runtime that reads/writes a git workspace.

## The surfaces (IA)

Three-region layout (left sidebar · main chat · right context rail), Claude-Code/Cursor style.

| Surface | What it is |
|---|---|
| **Live meeting** | During a call: proactive cards (new person / action item / decision), an entity cockpit, quick actions; live transcript in the right rail. |
| **Chat** | The agentic runtime — streaming turns with tools, `@`-mentions, git-commit badges, `[[wikilink]]` citations. |
| **Workspace** | Git-backed knowledge graph: people / companies / meetings / deals as wiki-linked markdown; documents are first-class. |
| **Inbox** | Email. The `Inbox triage` routine checks importance and surfaces **proposed actions on the message page**. |
| **Calendar** | Calendar = meetings. Day grid + flat List view; past events are recorded meetings that open their note. |
| **Tasks** | Action items from meetings, email, and routines — priority, due, source, done. |
| **Routines** | **Trigger → plan.** Time-triggered or event-triggered agents. Create one from chat with `/routine`. |

Setup is a vertical-templated wizard (Sales · HR · Financial markets → DTCC / Morgan Stanley / Citi) ending in a **Deployment** step (Cloud / VPC / air-gapped + BYO inference + the governance profile for that industry).

## Status

Early scaffold. **The prototype is the source of truth for look, feel, and interaction** — a finished, clickable single-file mock at [`public/prototype.html`](public/prototype.html). The Next.js app currently renders it full-bleed; real views are extracted into React incrementally (the strangler pattern).

```bash
# Prototype only (no deps, no backend):
open public/prototype.html
#   or serve it:  python3 -m http.server 8080  → http://localhost:8080/public/prototype.html

# The Next.js app (after deps land):
npm install
npm run dev            # http://localhost:3003  (renders the prototype today)
```

## Architecture

**Frontend** (this package — a Next.js composition root):
- Reuse the proven `clients/dashboard_new/modules/@vexa/dash-*` bricks for the meeting half: `dash-api-client` (gateway REST proxy), `dash-ws` (live transcript multiplex), `dash-meeting-state`, `dash-contracts`, `dash-transcript-viewer`, `dash-recording-players`, `dash-vnc-view`.
- Port the EI chat + workspace UI from `feature/ei-workspace-0.11` (`services/dashboard/src/components/workspace/*`: `ei-chat`, `wiki-markdown`, `file-panel`, `mention-menu`).
- New terminal modules: `tasks`, `routines` (trigger→plan), `inbox`, `calendar`, the `/`-skill command bar, and the setup wizard.

**Backend** (Vexa services — see [`docs/BACKEND.md`](docs/BACKEND.md) for the full gap analysis):
- **Have, production-ready in 0.12 `core/`:** gateway (auth/routing/WS), meeting-api (bots, transcripts, recordings), admin-api (users/tokens/webhooks), runtime (workload spawn), live transcription.
- **Have, but on the `ei-workspace` branch — must be ported into 0.12:** the agentic runtime (`agent-api`: SSE chat, per-org containers, workspace git/S3 sync, the knowledge-graph lineage), calendar-service (Google), the MCP server, the one-shot scheduler.
- **Missing — must be built:** email/inbox, tasks, the routines engine (cron execution + an event dispatcher beyond `meeting.completed`), real-time in-meeting extraction (today's lineage runs *post*-meeting), SSO/SCIM, a BYO-inference adapter for the agent LLM, and Outlook (email + calendar).

## Layout

```
clients/terminal/
  public/prototype.html     ← the clickable design SSOT (the mock)
  src/app/                  ← Next.js composition root (layout, page, globals)
  src/components/           ← extracted React views (grows from the prototype)
  src/lib/                  ← server seam (api proxy, config) — to add
  docs/BACKEND.md           ← what's needed vs. what Vexa has vs. what's missing
```
