# Paperclip vs Vexa: Agent Company OS vs Agent Runtime

> Competitive analysis — architecture, isolation, execution model, real-time data
>
> Research date: 2026-04-02

---

## TL;DR

**Paperclip** (43.7k ⭐) is a **company OS for AI agents** — it manages multiple agents as employees in an org chart with budgets, task checkout, approvals, and delegation. Think: the management layer.

**Vexa** is an **agent runtime** — it provides isolated containers, real-time data (meeting bots + transcription), and scalable infrastructure (Docker → K8s) for agents to execute safely. Think: the machine the agent runs on.

They solve adjacent but different problems. Paperclip orchestrates *who does what*. Vexa provides *where and how* agents execute.

**The gap:** Paperclip has zero container isolation — every agent runs as a local CLI process with full host access. Vexa solves exactly this.

---

## Side-by-Side Comparison

| Dimension | **Paperclip** | **Vexa** |
|---|---|---|
| **GitHub** | [paperclipai/paperclip](https://github.com/paperclipai/paperclip) — 43.7k ⭐, MIT | [Vexa-ai/vexa](https://github.com/Vexa-ai/vexa) — Apache 2.0 |
| **Core metaphor** | AI company — agents as employees | Agent runtime — containers as machines |
| **What it orchestrates** | Multiple agents in a hierarchy (CEO → managers → workers) | Individual agents in isolated containers |
| **One-liner** | "If OpenClaw is an employee, Paperclip is the company" | "If Claude Code is the agent, Vexa is the machine it runs on" |

---

## Claude Support

Both support Claude natively via the Claude Code CLI. The invocation is nearly identical:

### Paperclip — `claude_local` adapter

```typescript
// packages/adapters/claude-local/execute.ts
const args = [
  "--print", "-",
  "--output-format", "stream-json",
  "--verbose"
];
if (resumeSessionId) args.push("--resume", resumeSessionId);
if (dangerouslySkipPermissions) args.push("--dangerously-skip-permissions");
if (model) args.push("--model", model);
if (maxTurns > 0) args.push("--max-turns", String(maxTurns));
args.push("--add-dir", skillsDir);
```

Features: session persistence via `--resume`, skills injection via `--add-dir`, model selection, billing detection (API key vs subscription), auto-retry on stale sessions, cost tracking from `total_cost_usd`.

### Vexa — Agent API

```python
# services/agent-api/agent_api/chat.py
cmd = [
    "claude",
    "--output-format", "stream-json",
    "--allowedTools", tool_list,
    "-p", prompt
]
if session_id:
    cmd.extend(["--resume", session_id])
```

Features: SSE streaming to client, session state in Redis (7-day TTL), runs inside container via `docker exec`, workspace auto-synced to S3.

### Key difference

Paperclip spawns Claude as a **local process on the host**. Vexa spawns Claude **inside an isolated container** via `docker exec`. Same CLI, fundamentally different security posture.

---

## LLM Provider Support

| Provider | Paperclip | Vexa |
|---|---|---|
| Claude (Anthropic) | ✅ `claude_local` adapter | ✅ Native (Agent API wraps Claude CLI) |
| Codex (OpenAI) | ✅ `codex_local` adapter | ❌ |
| Gemini (Google) | ✅ `gemini_local` adapter | ❌ |
| Cursor | ✅ `cursor` adapter | ❌ |
| OpenCode | ✅ `opencode_local` adapter | ❌ |
| Hermes | ✅ `hermes_local` adapter | ❌ |
| Pi | ✅ `pi_local` adapter | ❌ |
| OpenClaw (remote) | ✅ `openclaw_gateway` adapter | ❌ |
| Arbitrary HTTP | ✅ `http` adapter (webhook) | ❌ |
| Arbitrary shell | ✅ `process` adapter | ❌ |

**Paperclip wins here** — 10 adapters, any CLI or HTTP agent. Vexa is Claude-only by design (the agent IS Claude Code running in a container).

---

## Container Isolation & Security

This is the critical architectural difference.

### Paperclip — No container isolation

```
┌──────────────────────────────────────┐
│           YOUR HOST MACHINE          │
│                                      │
│  ┌──────────┐  ┌──────────┐         │
│  │ Agent A  │  │ Agent B  │  ← all  │
│  │ (claude) │  │ (codex)  │    run  │
│  └──────────┘  └──────────┘    as   │
│  ┌──────────┐  ┌──────────┐  local  │
│  │ Agent C  │  │ Agent D  │  procs  │
│  │ (gemini) │  │ (cursor) │         │
│  └──────────┘  └──────────┘         │
│                                      │
│  📁 full filesystem access           │
│  🔑 all credentials accessible       │
│  🌐 full network access              │
└──────────────────────────────────────┘
```

- Agents spawn as child processes via Node.js `child_process`
- Plugin sandbox uses `vm.createContext()` (Node VM) — but this is for plugins, not agent execution
- No resource limits (CPU, memory) on agent processes
- No network isolation between agents
- All agents share the host filesystem
- The Docker image packages the *entire platform + all agent CLIs* into a single container — not per-agent containers

### Vexa — Full container isolation

```
┌──────────────────────────────────────┐
│           HOST / KUBERNETES          │
│                                      │
│  ┌─────────────┐  ┌─────────────┐   │
│  │ Container A │  │ Container B │   │
│  │ ┌─────────┐ │  │ ┌─────────┐ │   │
│  │ │ Agent   │ │  │ │ Agent   │ │   │
│  │ │ (claude)│ │  │ │ (claude)│ │   │
│  │ └─────────┘ │  │ └─────────┘ │   │
│  │ /workspace  │  │ /workspace  │   │
│  │ (user A)    │  │ (user B)    │   │
│  └─────────────┘  └─────────────┘   │
│  Resource limits   Resource limits   │
│  Network isolated  Network isolated  │
│  Auto-stop idle    Auto-stop idle    │
└──────────────────────────────────────┘
```

- Each user gets their own ephemeral container
- Profile-based resource limits (CPU, memory, GPU)
- Containers auto-stop after idle timeout (default 300s)
- Callback on exit for lifecycle management
- Three backends: Docker (dev), Kubernetes (prod), Process (embedded)
- Container can't access host filesystem, other users' data, or other containers

### Why this matters

Paperclip's 43.7k-star platform runs every agent with full host access. If one agent is compromised (prompt injection, jailbreak, malicious task), it has access to every other agent's data, all credentials, and the host machine.

Vexa's architecture means a compromised agent can only damage its own sandbox. The blast radius is one container, not the entire machine.

---

## Execution Model

### Paperclip — Heartbeat pattern

```
Trigger (cron/event/manual)
    │
    ▼
Budget check (auto-pause at 100%)
    │
    ▼
Spawn CLI process (claude --print --output-format stream-json)
    │
    ▼
Agent follows "Heartbeat Procedure":
  1. GET /api/agents/me          (identity)
  2. GET /api/agents/me/inbox-lite (assignments)
  3. POST /api/issues/{id}/checkout (claim task — 409 if taken)
  4. GET /api/issues/{id}/heartbeat-context (read context)
  5. Do work (using agent's native tools)
  6. Update status, post comments
  7. Delegate subtasks if needed
    │
    ▼
Process exits — run recorded in heartbeat_runs table
    │
    ▼
No persistent state. Next heartbeat starts fresh (with --resume for session continuity)
```

- Agent process lives only for the duration of one heartbeat
- No persistent container — spawn, work, exit
- Session continuity via Claude's `--resume` flag
- Cost tracked per heartbeat run

### Vexa — Persistent container with idle timeout

```
User sends chat message
    │
    ▼
Container exists for user?
    ├── No → Spawn container, sync workspace from S3
    └── Yes → Reuse existing container
    │
    ▼
docker exec: claude --output-format stream-json -p "message"
    │
    ▼
SSE stream response back to client
    │
    ▼
Container stays alive (idle timer reset)
    │
    ▼
No activity for 300s → Container auto-stops → Callback fires
    │
    ▼
Workspace already synced to S3 (60s periodic + on-save)
```

- Container persists across multiple chat messages
- Workspace state (files, git, packages) preserved between messages
- Idle timeout manages lifecycle — no manual stop needed
- Zero cost when no users are active

### Key difference

Paperclip: process per heartbeat, stateless between runs. Good for discrete tasks.
Vexa: container per user, stateful across messages. Good for interactive sessions and ongoing work.

---

## Multi-Agent Coordination

### Paperclip — Core feature

This is Paperclip's main value proposition:

| Feature | How it works |
|---|---|
| **Org chart** | Agents have `reportsTo` relationships, roles (ceo, general), titles |
| **Task checkout** | `POST /api/issues/{id}/checkout` — atomic, returns 409 if another agent owns it |
| **Delegation** | Agents create subtasks and assign to other agents |
| **@-mentions** | `@agent-name` in comments triggers that agent's heartbeat |
| **Approvals** | Request → approve/reject workflow with human-in-the-loop gates |
| **Budgets** | Per-agent monthly spending caps, auto-pause at 100% |
| **Inbox** | Each agent has an inbox with prioritized work (in_progress > todo > blocked) |
| **Company templates** | Export/import entire company configs (Clipmart marketplace) |

### Vexa — Not a focus

Vexa has no inter-agent coordination. One agent per user, one container per agent. Agents don't know about each other. Coordination happens outside Vexa (via the application layer, MCP tools, or scheduled callbacks).

### When each model wins

**Paperclip wins when:** You need multiple specialized agents working together — a "CEO agent" delegates to a "frontend agent" and a "backend agent," they work in parallel on different git worktrees, and a "QA agent" reviews their PRs.

**Vexa wins when:** You need isolated, secure execution with real-time data access — an agent that joins meetings, processes transcripts, builds knowledge graphs, and schedules itself for future work. All in a sandbox that scales from laptop to cluster.

---

## Workspace & File Management

### Paperclip — Git worktrees per task

```
project-repo/
├── .git/worktrees/
│   ├── issue-42/       ← Agent A's workspace (branch: paperclip/issue-42)
│   ├── issue-43/       ← Agent B's workspace (branch: paperclip/issue-43)
│   └── issue-44/       ← Agent C's workspace (branch: paperclip/issue-44)
└── main branch         ← shared project primary
```

- Two strategies: `project_primary` (shared dir) or `git_worktree` (isolated branch per task)
- Child tasks inherit workspace from parent
- Close-readiness inspection: checks dirty files, unpushed commits before teardown
- Custom `provisionCommand` and `teardownCommand` hooks
- No cloud persistence — all local

### Vexa — S3 + git per user

```
S3 bucket:
├── workspaces/user-1/     ← synced to container's /workspace
├── workspaces/user-2/
└── workspaces/user-3/

Container /workspace:
├── .git/                  ← version controlled
├── entities/
├── meetings/
├── CLAUDE.md
└── ...
```

- Sync down from S3 on container creation
- Sync up on explicit save + 60s periodic auto-sync
- Git commits on explicit workspace save (timestamped)
- Container can be destroyed and workspace survives
- Optional git remote push (user-configurable repo + token)

### Key difference

Paperclip: git-native, task-scoped branches, local only.
Vexa: cloud-persistent, user-scoped, survives container destruction.

---

## Scheduling

### Paperclip — In-process cron

```typescript
// 5-field cron expressions
{
  "triggers": [
    { "cron": "0 9 * * *", "timezone": "America/New_York" }
  ],
  "issueTemplate": {
    "title": "Daily standup sync",
    "description": "Check all agents' progress"
  }
}
```

- Standard cron expressions, timezone-aware
- Routines create issues on schedule → triggers agent heartbeats
- Catch-up logic: up to 25 missed runs executed on recovery
- Event-based triggers: task assignment, @-mention, approval
- In-process (Node.js) — dies with the server process
- No external queue (no Redis, no Kafka)

### Vexa — Redis sorted-set + HTTP callbacks

```bash
curl -X POST /scheduler/jobs -d '{
  "execute_at": 1712048400,
  "request": {
    "method": "POST",
    "url": "http://agent-api:8100/internal/chat",
    "body": {"user_id": "user-1", "message": "Daily sync"}
  },
  "retry": {"max_attempts": 3, "backoff": [30, 120, 300]},
  "idempotency_key": "daily-sync-2026-04-03"
}'
```

- Redis-backed (survives process restarts)
- HTTP callback to any URL (with SSRF protection)
- Exponential backoff retry
- Idempotency keys (7-day TTL)
- Background executor polls every second
- Cron support via metadata (future expansion)

### Key difference

Paperclip: in-process, tightly integrated with task system, dies with server.
Vexa: persistent, decoupled HTTP callbacks, survives restarts, arbitrary targets.

---

## Meeting Bots & Real-Time Data

### Paperclip — None

Zero meeting, audio, video, or real-time data capture capability. Paperclip is purely a task orchestration platform. Agents get context from the Paperclip API (task descriptions, comments, documents) and from whatever files are in their workspace.

### Vexa — Full meeting bot lifecycle

```bash
# Send a bot to join a Google Meet
curl -X POST /bots -d '{
  "platform": "google_meet",
  "native_meeting_id": "abc-defg-hij"
}'

# Bot joins → captures audio → transcribes in real-time (<5s)

# Make the bot speak
curl -X POST /bots/google_meet/abc-defg-hij/speak \
  -d '{"text": "Thanks for joining. Taking notes.", "voice": "alloy"}'

# Get live transcript
curl /transcripts/google_meet/abc-defg-hij
# → segments with speaker labels, timestamps, confidence scores

# Agent receives meeting context via X-Meeting-Context header
# Latest 30 transcript segments injected per chat request
```

Supported platforms: Google Meet, Microsoft Teams, Zoom
Bot capabilities: speak (TTS), send/read chat, share screen, set avatar
Architecture: Chromium container + Playwright + Whisper transcription
Lifecycle: requested → joining → awaiting_admission → active → completed

### Why this matters

Paperclip agents can only work with data that already exists in files or is provided via the task system. Vexa agents have real-time access to live conversations — they can join meetings, listen, respond, and build knowledge while the meeting is happening.

---

## MCP (Model Context Protocol)

### Paperclip — No native MCP

No MCP server or client in the codebase. Paperclip has a plugin system with namespaced tools (`acme.linear:search-issues`) and a sandboxed plugin runtime, but this is a proprietary tool format, not MCP.

Agents running under Paperclip CAN use MCP if their underlying CLI supports it (e.g., Claude Code's own MCP config), but Paperclip doesn't manage or expose MCP connections.

### Vexa — 32+ MCP tools

Dedicated MCP server (port 8010) with 7 tool categories:

| Category | Tools | Examples |
|---|---|---|
| Meeting Management | 7 | `request_meeting_bot`, `stop_bot`, `list_meetings`, `get_bot_status` |
| Transcripts & Sharing | 3 | `get_meeting_transcript`, `get_meeting_bundle`, `create_transcript_share_link` |
| Recordings | 6 | `list_recordings`, `get_recording`, `transcribe_recording` |
| Interactive Bot Control | 7 | `bot_speak`, `send_chat_message`, `bot_screen_share`, `set_bot_avatar` |
| Calendar | 5 | `calendar_connect`, `list_calendar_events`, `schedule_meeting` |
| Webhooks | 2 | `configure_webhook`, `transcribe_recording` |
| Bot Config | 1 | `update_bot_config` |

Works with Claude Desktop, Cursor, Windsurf, or any MCP-compatible client. Standard bearer token auth.

---

## Tech Stack

| Layer | **Paperclip** | **Vexa** |
|---|---|---|
| Language | TypeScript (full stack) | Python (backend) + TypeScript (dashboard) |
| Server | Express.js 5 | FastAPI (per service) |
| Frontend | React 19, Vite 6, React Router 7 | Next.js 16, React 19 |
| UI | Radix UI, Tailwind CSS 4 | Radix UI, Tailwind CSS 4 |
| Database | PostgreSQL 17 or PGlite (embedded) | PostgreSQL |
| ORM | Drizzle | SQLAlchemy / raw SQL |
| Cache/Queue | None — in-memory EventEmitter | Redis (state, scheduler, pub/sub, streams) |
| Storage | Local filesystem only | S3/MinIO (recordings, workspaces) |
| Auth | Better Auth (sessions + API keys) | Custom scoped tokens + admin keys |
| Architecture | **Monolith** (single Node.js process) | **Microservices** (8+ services) |
| Container | Single Docker image (platform + all CLIs) | Separate containers per service + per agent |

---

## Architecture Diagram

### Paperclip

```
┌────────────────────────────────────────────────┐
│              Paperclip (single process)         │
│                                                 │
│  ┌──────────┐  ┌───────────┐  ┌─────────────┐  │
│  │ React UI │  │ Express   │  │ PostgreSQL  │  │
│  │ (Vite)   │  │ REST API  │  │ (or PGlite) │  │
│  └──────────┘  └─────┬─────┘  └─────────────┘  │
│                       │                          │
│              ┌────────┼────────┐                 │
│              │        │        │                 │
│         ┌────▼───┐ ┌──▼───┐ ┌──▼─────┐          │
│         │ claude │ │codex │ │ gemini │  ← local  │
│         │  CLI   │ │ CLI  │ │  CLI   │   procs   │
│         └────────┘ └──────┘ └────────┘           │
│                                                  │
│  No isolation between agents                     │
│  No resource limits                              │
│  Full host access                                │
└────────────────────────────────────────────────┘
```

### Vexa

```
┌─────────────────────────────────────────────────────────────┐
│                        Vexa Platform                        │
│                                                             │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌─────────┐  │
│  │ Dashboard │  │ API       │  │ Admin API │  │ MCP     │  │
│  │ (Next.js) │  │ Gateway   │  │ (tokens)  │  │ Server  │  │
│  └───────────┘  └─────┬─────┘  └───────────┘  └─────────┘  │
│                        │                                     │
│          ┌─────────────┼──────────────┐                      │
│          │             │              │                      │
│    ┌─────▼──────┐ ┌────▼──────┐ ┌─────▼────────┐            │
│    │ Meeting API│ │ Agent API │ │ Runtime API  │            │
│    │ (bots)     │ │ (chat)    │ │ (containers) │            │
│    └─────┬──────┘ └─────┬─────┘ └──────┬───────┘            │
│          │              │              │                     │
│          └──────────────┼──────────────┘                     │
│                         │                                    │
│              ┌──────────┼──────────┐                         │
│              │          │          │                         │
│         ┌────▼───┐ ┌────▼───┐ ┌────▼───┐                    │
│         │Container│ │Container│ │Container│  ← isolated      │
│         │ User A │ │ User B │ │ Bot    │    per-user        │
│         │ /work  │ │ /work  │ │ Meet   │    containers      │
│         └────────┘ └────────┘ └────────┘                    │
│         CPU/MEM     CPU/MEM    CPU/MEM                      │
│         limits      limits     limits                       │
│                                                             │
│  ┌────────┐  ┌──────┐  ┌───────┐  ┌───────┐  ┌──────────┐  │
│  │ Redis  │  │Postgres│ │ MinIO │  │ TTS   │  │Whisper   │  │
│  └────────┘  └──────┘  └───────┘  └───────┘  └──────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Where They Complement Each Other

These aren't competitors — they're different layers of the stack. A combined architecture would be powerful:

```
┌──────────────────────────────────────────────┐
│         Paperclip (orchestration)             │
│                                               │
│  Org chart, budgets, task checkout,           │
│  delegation, approvals, multi-agent           │
│                                               │
│  Agent A needs to execute a task...           │
│         │                                     │
│         ▼                                     │
│  ┌─────────────────────────────────────────┐  │
│  │ Vexa Runtime API (execution)            │  │
│  │                                         │  │
│  │ Spawn isolated container, execute work, │  │
│  │ access meeting transcripts, auto-stop   │  │
│  └─────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

A hypothetical `vexa_runtime` adapter for Paperclip would:
1. Replace local CLI spawning with `POST /containers` → `POST /containers/{name}/exec`
2. Give every Paperclip agent container isolation + resource limits
3. Add real-time meeting data via Vexa's Meeting API
4. Enable Kubernetes scaling for Paperclip's agent fleet
5. Add idle management — agents consume zero resources when not working

---

## Summary for the Presentation

**The narrative hook:**

> "Paperclip got 43,000 stars by solving the orchestration problem — multiple agents working as a company. CEO delegates to managers, managers delegate to workers, budgets track spending, approvals keep humans in the loop.
>
> But look at how agents actually execute: local CLI processes on your host machine. Full filesystem access. No resource limits. No isolation between agents.
>
> That's exactly the problem we described earlier. The orchestration is solved. The execution layer is not.
>
> Vexa is that execution layer. Ephemeral containers. Per-user isolation. Real-time meeting data. Docker on your laptop, Kubernetes in production. Same API.
>
> These aren't competitors — they're complementary. Paperclip decides *who does what*. Vexa provides *where and how* they do it safely."

---

## Sources

- [Paperclip GitHub — 43.7k ⭐](https://github.com/paperclipai/paperclip)
- [Paperclip Documentation](https://docs.paperclip.ing)
- [Paperclip Architecture](https://docs.paperclip.ing/start/architecture)
- [Claude Local Adapter Source](https://github.com/paperclipai/paperclip/tree/main/packages/adapters/claude-local)
- [Towards AI — Paperclip Deep Dive](https://pub.towardsai.net/paperclip-the-open-source-operating-system-for-zero-human-companies-2c16f3f22182)
- [Medium — Paperclip Analysis](https://medium.com/@creativeaininja/paperclip-the-open-source-platform-turning-ai-agents-into-an-actual-company-7348015c5bf7)
- [Vexa GitHub](https://github.com/Vexa-ai/vexa)
