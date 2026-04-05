# From Claude Code to Agent Runtime: Making AI Agents Scalable and Secure

> YouTube Presentation — Script + Slides + Code Examples
>
> Style reference: Cole Medin, Nate Herk — conversational, developer-focused, visual

---

## META

- **Duration:** ~12-15 min
- **Format:** Talking head + screen recordings + slide overlays
- **Tone:** Builder sharing insights, not a pitch. "Here's what I learned building this."
- **Thread:** 3 components → not just code → 4th component → problems → solution → demo

---

# COLD OPEN

## [SLIDE: Hook]

**On screen:**
> Everyone is coding on Claude Code.
> How do we make it scalable?
>
> google meet &nbsp; ms teams &nbsp; zoom &nbsp; streaming
> *anything streamed in browser*
> <5 sec latency
> WS &nbsp; API &nbsp; MCP

**Narration:**

Everyone's coding on Claude Code. Cursor, Windsurf, Codex — AI agents writing real production software. That's not hype anymore, that's Tuesday.

But here's the thing nobody's talking about: **this setup isn't just for code.** And once you realize that, you hit a wall that nobody's solved cleanly yet.

Let me show you what I mean.

---

# ACT 1: THE THREE COMPONENTS

## [SLIDE: What Makes AI Coding Work]

**On screen:**
```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│   Machine    │   │  CLI Agent   │   │  Files/Code  │
│              │   │              │   │              │
│  (executes)  │   │  (reasons)   │   │  (context)   │
└──────────────┘   └──────────────┘   └──────────────┘
```

**Narration:**

Strip away all the marketing and what do you actually need to build software with AI?

Three things. That's it.

**One — a machine.** Something that can execute code, run commands, interact with the file system. Your laptop. A VM. A container.

**Two — a CLI agent.** Claude Code, Codex, Aider — the thing that reads context, decides what to do, calls tools, and loops until it's done.

**Three — files.** A folder with code. That's the agent's workspace. It reads, writes, commits. The folder IS the interface.

That's the entire stack. Machine, agent, files. Everything else is a wrapper around these three.

---

## [SLIDE: The Agent Loop]

**On screen:**
```python
while not done:
    action = llm(context, tools=[read, write, bash])
    result = execute(action)
    context += result
```

**Narration:**

And the agent itself? It's a loop. LLM picks an action from available tools. Execute it. Feed the result back. Repeat until done.

The magic is never in the loop. The magic is in **what tools** you give it and **what data** it operates on.

For coding, that's file system access and a bash shell. The data is your codebase. And it works — demonstrably, shipping production code, today.

---

# ACT 2: IT'S NOT JUST FOR CODE

## [SLIDE: LLMs Are General Text Processors]

**On screen:**
| Domain | The "Codebase" | The "Tests" |
|---|---|---|
| **Software** | .py, .ts, .go files | pytest, jest, compiler |
| **Science** | .tex papers, .bib refs, data/ | LaTeX builds, peer review checklist |
| **CRM** | contacts/, companies/, deals/ | Entity completeness, link integrity |
| **Legal** | contracts/, clauses/, precedents/ | Clause coverage, conflict detection |
| **Knowledge** | entities/, meetings/, action-items/ | Wiki-link resolution, staleness audit |

**Narration:**

Here's the insight that changes everything: large language models are **general text processors.** They're not built exclusively for code. They work on any structured text with the same proficiency.

So what happens if the folder isn't code? What if it's a scientific paper? A CRM? A knowledge base?

**It still works.** It's the same setup. Claude Code doesn't care whether it's editing Python or Markdown. It reads, reasons, writes, and commits.

The "codebase" can be a folder full of contact cards and meeting notes. The "tests" can be wiki-link resolution and entity completeness checks. Same agent loop, different domain.

You can literally use Claude Code right now to manage a CRM, write a research paper, or build a knowledge base. Point it at a folder, give it a CLAUDE.md with instructions, and it goes.

---

## [SLIDE: Real Example — Knowledge From Meetings]

**On screen:**
```
knowledge/
├── entities/
│   ├── contacts/
│   │   ├── brian-steele.md      # EVP at DTCC
│   │   └── joe-lubin.md         # Founder, ConsenSys
│   ├── companies/
│   │   ├── dtcc.md              # $100T+ in custody
│   │   └── consensys.md         # Ethereum ecosystem
│   └── products/
│       └── first-person-project.md
├── meetings/
│   └── dtcc-consensys-panel.md  # Auto-extracted summary
└── action-items/
    └── 2026-02-11-toip.md       # Owner, status, due date
```

**Narration:**

Here's a real example. We took 10 public YouTube recordings — FINOS panels, steering committee meetings — and pointed a coding agent at them.

Same agent loop. Read transcript, extract entities, create markdown files with structured fields and wiki-links. Update existing entities when they appear again.

Ten recordings produced 122 interconnected entities. 55 contacts, 36 companies, 11 products. Each one linked to the others with wiki-links. Each meeting enriches the next one — the 10th recording produces richer output than the 1st because it has 9 recordings of accumulated context.

**This is not summarization. This is knowledge graph construction — with a coding agent.**

---

# ACT 3: THE FOURTH COMPONENT

## [SLIDE: Enter the Scheduler]

**On screen:**
```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│   Machine    │   │  CLI Agent   │   │    Files     │   │  Scheduler   │
│              │   │              │   │              │   │              │
│  (executes)  │   │  (reasons)   │   │  (context)   │   │  (triggers)  │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
```

**Narration:**

Then a fourth component appeared. People realized this when Claude Code released remote agents and scheduling. Other tools followed.

**The scheduler.** The agent can now schedule itself. It can say "run me again tomorrow at 9am" or "run me whenever a new meeting recording appears."

This changes everything. The agent is no longer a one-shot tool you invoke manually. It's a **persistent process** that can:

- Respawn itself in the future
- Run on a cron schedule
- Scale by spawning other workers
- React to events

That knowledge base we built from 10 recordings? With a scheduler, it updates itself. New meeting happens → agent runs → entities extracted → knowledge graph grows. Indefinitely. Without human intervention.

**This is what blew people's minds.** Not that agents can code — that agents can **live.**

---

# ACT 4: THE PROBLEMS

## [SLIDE: Problem 1 — Security]

**On screen:**
```
┌─────────────────────────────────┐
│         YOUR MACHINE            │
│                                 │
│   ┌───────────┐                 │
│   │   Agent   │ ← full access   │
│   │           │   to everything │
│   └───────────┘                 │
│                                 │
│   📁 your files                 │
│   🔑 your credentials           │
│   🌐 your network               │
│   💳 your cloud accounts         │
│   🖥️ your other services         │
│                                 │
└─────────────────────────────────┘
```

**Narration:**

But then the problems hit.

**Security.** Your Claude Code agent has full access to your machine. Everything. Files, credentials, network, cloud accounts. That's fine when YOU are sitting there watching it. But a scheduled agent? Running at 3am? Triggered by an external event?

Agents are non-deterministic. They can do unexpected things. They can be manipulated via prompt injection. One compromised agent equals compromised infrastructure.

This isn't theoretical. This is the reason most enterprises won't deploy autonomous agents today.

---

## [SLIDE: Problem 2 — Scalability]

**On screen:**
```
Agent lifecycle:
  ████░░░░░░░░░░░░░░░░░░░░░░░░░░  active (~5%)
  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  idle (~95%)

Resources allocated: 8 GB RAM, 4 CPU cores
Resources used:      200 MB for 3 minutes, then nothing
```

**Narration:**

**Scalability.** Why do we need to dedicate an 8 GB VM to an agent that's dormant 95% of its lifecycle? That's 200 MB of actual work for 3 minutes, then the machine sits there burning money.

One machine, one agent. Can't serve multiple users. Can't scale horizontally. Can't share resources.

Gmail serves billions of users on shared infrastructure. A thing that needs an **entire machine** to think for 3 minutes is the opposite of scalable.

---

## [SLIDE: Problem 3 — Over-privileged Access]

**On screen:**
```
What the agent NEEDS:        What the agent GETS:
─────────────────────        ─────────────────────
✅ Read/write workspace      🔓 Full filesystem access
✅ Bash in sandbox            🔓 Root on host machine
✅ Network to LLM API        🔓 Access to all services
✅ Git push to one repo       🔓 All your SSH keys
```

**Narration:**

And **over-privileged access.** The agent needs to read and write files in a workspace. Maybe run some bash commands. Call an LLM API. Push to a git repo.

What does it actually get? Your entire machine. Root access. All your SSH keys. Every service on your network.

Why are we giving it access to things it doesn't need? Why don't we give it an isolated, carefully selected environment for safe operations?

That's the question that led to what we built.

---

# ACT 5: THE SOLUTION — AGENT RUNTIME

## [SLIDE: The Idea]

**On screen:**
```
Instead of:  Agent → YOUR Machine
Do this:     Agent → Ephemeral Container → Destroyed after use
                     ├── workspace files (persisted to S3/git)
                     ├── sandboxed bash
                     ├── LLM access
                     └── nothing else
```

**Narration:**

The answer is **a runtime for agents.** Not your machine. Not a dedicated VM. An ephemeral, isolated container that fires up when the agent needs to work, and gets destroyed when it's done.

The workspace files persist — synced to S3, version controlled in git. But the compute? Ephemeral. Spin up, do the work, tear down. Zero cost when idle. Full isolation between users. No access to anything outside the sandbox.

Same three components — machine, agent, files — but the machine is now **managed infrastructure**, not your laptop.

---

## [SLIDE: Three APIs, One Platform]

**On screen:**
```
┌─────────────────────────────────────────────────────┐
│                    Your App / Agent                  │
└────────────┬──────────────┬──────────────┬──────────┘
             │              │              │
     ┌───────▼──────┐ ┌────▼──────┐ ┌─────▼────────┐
     │  Meeting API │ │ Agent API │ │  Runtime API  │
     │              │ │           │ │               │
     │  Bots in     │ │ Sandboxed │ │  Universal    │
     │  meetings    │ │ AI agents │ │  infra layer  │
     └──────────────┘ └───────────┘ └───────────────┘
```

**Narration:**

We built three APIs that solve this cleanly.

**The Runtime API** is the foundation. It manages container lifecycle — spawn, exec, stop, cleanup. It speaks Docker on your laptop, Kubernetes in the cloud, or raw processes on a phone. One API, three backends.

**The Agent API** is Claude Code as a service. Send a message, get back a streaming response. The agent runs inside an ephemeral container with its own workspace. Isolated. Scheduled. Scalable.

**The Meeting API** puts bots in meetings. Google Meet, Microsoft Teams, Zoom — anything streamed in a browser. Real-time transcription under 5 seconds of latency. The bot is a container too — same lifecycle, same isolation.

Let me show you each one.

---

# ACT 6: RUNTIME API — THE INFRASTRUCTURE LAYER

## [SLIDE: Runtime API]

**On screen (left side):**
> **Runtime API**
> *Universal Infrastructure Layer*
>
> 🖥️ Docker &nbsp; ☸️ Kubernetes &nbsp; ⚙️ Process

**On screen (right side) — architecture diagram:**
```
              ┌─────────────┐
              │ Runtime API  │
              └──────┬───────┘
           ┌─────────┼─────────┐
           │         │         │
     ┌─────▼──┐ ┌────▼───┐ ┌──▼──────┐
     │ Docker │ │  K8s   │ │ Process │
     └────────┘ └────────┘ └─────────┘
```

**Narration:**

The Runtime API is the infrastructure layer. It doesn't care what's inside the container — a coding agent, a meeting bot, a data pipeline. It manages the lifecycle.

---

## [SLIDE: Runtime API — Code Example]

**On screen — code:**
```bash
# Spawn a container
curl -X POST https://runtime.vexa.ai/containers \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "agent",
    "user_id": "user-123",
    "callback_url": "https://api.vexa.ai/hooks/container",
    "metadata": {"session_id": "sess-abc"}
  }'
```
```json
{
  "name": "agent-user-123-a1b2c3",
  "profile": "agent",
  "status": "running",
  "ports": {"8080/tcp": 32100},
  "created_at": 1711875600.0
}
```

**Narration:**

One POST to `/containers` with a profile name. The Runtime API picks the right image, applies resource limits, sets up networking, and returns a running container in seconds.

Profiles define everything — image, CPU, memory, idle timeout, GPU if needed. An `agent` profile gets 200 MB and a 5-minute idle timeout. A `browser` profile for meeting bots gets 1.5 GB and a Chromium browser. A `gpu-compute` profile gets an NVIDIA GPU for ML inference.

---

## [SLIDE: Runtime API — Lifecycle Management]

**On screen:**
```bash
# Execute a command inside the container
curl -X POST https://runtime.vexa.ai/containers/agent-user-123-a1b2c3/exec \
  -d '{"command": ["claude", "--message", "process the latest transcript"]}'

# Heartbeat — reset the idle timer
curl -X POST https://runtime.vexa.ai/containers/agent-user-123-a1b2c3/touch

# When done — container auto-stops after idle timeout
# Or force stop:
curl -X DELETE https://runtime.vexa.ai/containers/agent-user-123-a1b2c3
```

**Narration:**

Containers auto-stop after their idle timeout. No heartbeat? Container dies. Zero cost when nobody's using it.

When a container exits — whether from idle timeout, explicit stop, or crash — the Runtime API fires a callback to your service. You know exactly when containers come and go. Full lifecycle observability.

The scaling story is simple:

| Your laptop | Self-hosted server | Cloud |
|---|---|---|
| Process backend | Docker backend | Kubernetes backend |
| 1 agent | 10s of containers | Thousands of pods |

**Same API. Same code. Different backend.** You develop locally with Docker, deploy to production with Kubernetes, no code changes.

---

# ACT 7: AGENT SANDBOX API

## [SLIDE: Agent Sandbox API]

**On screen (left side):**
> **Agent Sandbox API**
> *Claude Code as a service*
>
> isolated &nbsp; scheduled &nbsp; user files &nbsp; scalable

**Narration:**

The Agent API wraps this into something immediately useful. It's Claude Code as a service — but isolated in a sandbox with its own workspace.

You send a chat message. The API ensures a container is running for that user, executes the agent inside it, and streams the response back. When the agent is done, the workspace persists to S3. The container can die.

---

## [SLIDE: Agent API — Chat]

**On screen:**
```bash
# Chat with an agent — streaming response
curl -N -X POST https://api.vexa.ai/api/chat \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-123",
    "message": "Process the latest meeting transcript and update the knowledge base",
    "session_id": "sess-abc"
  }'
```
```
data: {"type": "text_delta", "text": "I'll process the transcript..."}
data: {"type": "tool_use",  "name": "read_file", "input": {"path": "meetings/latest.md"}}
data: {"type": "text_delta", "text": "Found 3 new contacts..."}
data: {"type": "tool_use",  "name": "write_file", "input": {"path": "entities/contacts/..."}}
data: {"type": "done",      "session_id": "sess-abc"}
```

**Narration:**

SSE streaming. You see every tool call the agent makes in real time. Read file, write file, bash command — all visible, all inside the sandbox.

The agent has full power inside its container. It can install packages, run scripts, call external APIs. But it can't touch your host machine, other users' data, or anything outside its sandbox.

---

## [SLIDE: Agent API — Workspace]

**On screen:**
```bash
# Read what the agent produced
curl "https://api.vexa.ai/api/workspace/files?user_id=user-123" \
  -H "X-API-Key: $API_KEY"

# Response:
{
  "files": [
    "entities/contacts/brian-steele.md",
    "entities/contacts/joe-lubin.md",
    "entities/companies/dtcc.md",
    "entities/companies/consensys.md",
    "meetings/dtcc-consensys-panel.md",
    "action-items/2026-02-11-toip.md",
    "CLAUDE.md"
  ]
}
```

**Narration:**

The workspace is just files. You can read them, download them, put them in a git repo. Human-in-the-loop via git diff — you review what the agent changed before accepting it. Same workflow developers already use.

---

## [SLIDE: Agent API — Scheduling]

**On screen:**
```bash
# Schedule the agent to run tomorrow at 9am
curl -X POST https://api.vexa.ai/scheduler/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "execute_at": 1712048400,
    "request": {
      "method": "POST",
      "url": "http://agent-api:8100/internal/chat",
      "body": {
        "user_id": "user-123",
        "message": "[scheduled:github-sync] Check for new activity and update knowledge base"
      }
    },
    "retry": {
      "max_attempts": 3,
      "backoff": [30, 120, 300]
    }
  }'
```

**Narration:**

And here's the scheduler — the fourth component. The agent can schedule itself. "Run me tomorrow at 9am." "Run me every time a new meeting finishes."

The scheduler fires an HTTP callback at the scheduled time. The Agent API spins up a container, runs the agent, tears it down. Zero resources consumed between runs.

An agent that was costing you a dedicated VM 24/7? Now it costs you 3 minutes of container time per day. The other 23 hours and 57 minutes? Nothing.

---

# ACT 8: REAL-TIME TRANSCRIPTION BOTS API

## [SLIDE: Meeting API]

**On screen (left side):**
> **Real-time transcription bots API**
>
> google meet &nbsp; ms teams &nbsp; zoom &nbsp; streaming
> *anything streamed in browser*
> <5 sec latency
> WS &nbsp; API &nbsp; MCP

**On screen (right side):** Container architecture diagram + live transcript screenshot

**Narration:**

Agents need data. For our knowledge use case, that data comes from meetings. The Meeting API handles this — it puts bots in meetings and streams transcriptions in real time.

Google Meet, Microsoft Teams, Zoom — anything that runs in a browser. The bot is a container with Chromium and Playwright. It joins the meeting, captures audio per speaker, transcribes with Whisper, and streams segments back in under 5 seconds.

---

## [SLIDE: Meeting API — Send a Bot]

**On screen:**
```bash
# Send a bot to a Google Meet
curl -X POST https://api.vexa.ai/bots \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij"
  }'
```
```json
{
  "id": 42,
  "platform": "google_meet",
  "native_meeting_id": "abc-defg-hij",
  "status": "joining",
  "constructed_meeting_url": "https://meet.google.com/abc-defg-hij",
  "created_at": "2026-04-01T12:00:00Z"
}
```

**Narration:**

One API call. Platform and meeting ID. The Meeting API calls the Runtime API to spawn a browser container, the bot joins the meeting, and you start getting transcription segments.

---

## [SLIDE: Meeting API — Interactive Bot]

**On screen:**
```bash
# Make the bot speak in the meeting
curl -X POST https://api.vexa.ai/bots/google_meet/abc-defg-hij/speak \
  -H "X-API-Key: $API_KEY" \
  -d '{"text": "Thanks for joining. I will be taking notes.", "voice": "alloy"}'

# Send a chat message
curl -X POST https://api.vexa.ai/bots/google_meet/abc-defg-hij/chat \
  -d '{"text": "Action items will be posted after the meeting."}'

# Share a screen
curl -X POST https://api.vexa.ai/bots/google_meet/abc-defg-hij/screen \
  -d '{"url": "https://dashboard.example.com/live-summary"}'

# Get the live transcript
curl https://api.vexa.ai/transcripts/google_meet/abc-defg-hij \
  -H "X-API-Key: $API_KEY"
```

**Narration:**

But the bot isn't just a passive recorder. It's an interactive agent. It can speak with text-to-speech. Send chat messages. Share its screen. Read the meeting chat.

This turns a transcription bot into a **meeting participant** — one controlled by your agent pipeline.

---

## [SLIDE: Bot Lifecycle]

**On screen:**
```
POST /bots → Container Spawn → Join Meeting → Admitted?
                                                 │
                                          ┌──────┴──────┐
                                          │ Yes         │ No
                                          ▼             ▼
                                   Capture Audio    Retry/Timeout
                                          │
                              ┌────────────┼────────────┐
                              │            │            │
                          Transcribe    /speak       /screen
                              │         (TTS)     (share screen)
                              ▼
                           Store → DELETE /bots → Container Cleanup
```

**Narration:**

The full lifecycle: request, spawn, join, capture, transcribe, interact, stop, cleanup. Every stage has a status you can poll. Webhooks fire on state changes. When the meeting ends, the container is destroyed — zero cost.

---

# ACT 9: THE LOOP

## [SLIDE: Putting It All Together]

**On screen:**
```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────────┐
│   Capture    │     │  Observe & Act   │     │  Schedule Next Step  │
│              │────▶│                  │────▶│                      │
│ Meeting Bots │     │     Agent        │     │       Agent          │
└──────────────┘     └──────────────────┘     └──────────────────────┘
                                                        │
                                                        │ schedules
                                                        ▼
                                              ┌──────────────┐
                                              │   Capture    │
                                              │ Meeting Bots │
                                              └──────────────┘
```

**Narration:**

This is the complete loop. Three steps, running forever.

**Capture.** Meeting bots join your meetings, transcribe in real time. That's the Meeting API.

**Observe and Act.** An agent processes the transcript. Extracts entities. Updates the knowledge graph. Drafts follow-up emails. That's the Agent API.

**Schedule Next Step.** The agent schedules itself for the next meeting. Or schedules a daily sync. Or reacts to a webhook. That's the Scheduler.

All three run on the Runtime API. All isolated. All ephemeral. All scalable from a laptop to a Kubernetes cluster.

---

## [SLIDE: What This Enables]

**On screen:**

```bash
# The complete autonomous knowledge agent — 4 API calls

# 1. Schedule a daily sync
curl -X POST /scheduler/jobs -d '{
  "cron": "0 9 * * *",
  "request": {"method": "POST", "url": "/internal/chat",
    "body": {"message": "[scheduled:github-sync]"}}
}'

# 2. Schedule bot for upcoming meeting (from calendar)
curl -X POST /bots -d '{
  "platform": "google_meet",
  "native_meeting_id": "abc-defg-hij"
}'

# 3. After meeting — agent processes transcript
curl -X POST /api/chat -d '{
  "message": "Process meeting 42 transcript, update knowledge base"
}'

# 4. Agent reads workspace — knowledge graph updated
curl /api/workspace/files
# → entities/contacts/*, entities/companies/*, meetings/*, action-items/*
```

**Narration:**

Four API calls. That's a complete autonomous knowledge agent.

Schedule a daily sync. Send bots to meetings from your calendar. Process transcripts after each meeting. Read the growing knowledge graph whenever you need it.

The 10th meeting is richer than the 1st. The 100th is richer than the 10th. Knowledge compounds. And it's all just markdown files in a git repo that you can inspect, diff, and roll back.

---

# CLOSING

## [SLIDE: Why This Matters]

**On screen:**

| The old way | The new way |
|---|---|
| Agent runs on your laptop | Agent runs in ephemeral container |
| Full machine access | Sandboxed — only what it needs |
| Always-on VM, mostly idle | Zero cost when dormant |
| One user per machine | Thousands of agents per cluster |
| Manual invocation | Self-scheduling, event-driven |
| Code only | Any structured text domain |

**Narration:**

We started with a simple observation: AI coding works because of three components — machine, agent, files. That's not specific to code.

Then a fourth component appeared — the scheduler — and agents became autonomous. But that created security and scalability problems that nobody solved cleanly.

The answer is an agent runtime. Ephemeral containers, managed lifecycle, isolated sandboxes. Same agent, same tools, same power — but now it's safe, scalable, and always available.

We're open source. Apache 2.0. Fully self-hostable.

---

## [SLIDE: The Stack — Final Card]

**On screen:**
```
Vexa Agentic Runtime

Meeting API          Agent API           Runtime API
─────────────        ─────────           ───────────
Bots in meetings     Sandboxed agents    Docker / K8s / Process
Real-time TX         Chat + Workspace    Lifecycle management
Voice agent          Scheduling          Idle auto-cleanup
<5s latency          Git persistence     Callback hooks

32+ MCP tools  •  Dashboard  •  WebSocket + REST + SSE

github.com/Vexa-ai/vexa
```

**Narration:**

If you're building anything with AI agents — not just coding, but knowledge work, CRM, research, operations — and you need it to be safe, scalable, and autonomous, this is the infrastructure layer that makes it possible.

Link in the description. Star the repo. Try it out.

Thanks for watching.

---

# APPENDIX: B-ROLL & SCREEN RECORDING NOTES

## Suggested screen recordings

1. **Terminal demo** (30s): `curl -X POST /bots` → show bot joining a real Google Meet → transcript streaming in terminal
2. **Dashboard walkthrough** (30s): Show the Next.js dashboard — meeting list, live transcript viewer, agent chat interface
3. **Workspace demo** (20s): Show the knowledge base files in VS Code or the dashboard file browser — entities linked with wiki-links
4. **Architecture diagram** (hold 5s): The three-tier API diagram from the slides
5. **Container lifecycle** (20s): `docker ps` showing containers spinning up and auto-stopping

## Suggested overlays

- Meeting platform logos (Google Meet, Teams, Zoom) when discussing multi-platform
- Code snippets appearing as you narrate the curl examples
- Before/after split: "Agent on your laptop" vs "Agent in sandbox"
- Counter animation: "122 entities from 10 recordings"

## Thumbnail options

- "Everyone is coding on Claude Code. How do we make it scalable?"
- Terminal with streaming transcript + meeting bot in Google Meet (split screen)
- Three-tier API diagram with "Open Source Agent Runtime" text

---

# PRODUCTION GUIDE: HOW THE TOP CREATORS DO IT

## The Format — What Works in 2026

Based on research into Cole Medin, Nate Herk, Net Ninja, Edmund Yong, and the broader Claude Code YouTube ecosystem (25+ channels ranked, 4K–853K views range):

### Cole Medin's Format

**Channel:** [@ColeMedin](https://www.youtube.com/@ColeMedin) — AI Agents Masterclass series
**Style:** Screen recording + talking head overlay (bottom-right corner)
**Key tool:** Excalidraw for ALL diagrams — hand-drawn aesthetic, not polished slides

**His production workflow:**
1. **Excalidraw diagrams** — not PowerPoint, not Keynote. Hand-drawn look. He built a Claude Code skill ([excalidraw-diagram-skill](https://github.com/coleam00/excalidraw-diagram-skill)) that generates diagrams from natural language
2. **Render-validate loop** — agent generates Excalidraw JSON → renders to PNG via Playwright → reviews its own output → fixes layout issues (overlapping text, misaligned arrows) → repeats until clean
3. **Color palette** — customizable via `color-palette.md` — match your brand
4. **Live coding** — most of the video is him in a terminal, with diagrams shown as context-setting before/between coding segments
5. **GitHub companion** — every video has a repo folder with the code shown

**What makes his videos work:**
- "Argue visually" — every shape mirrors the concept (fan-outs for one-to-many, timelines for sequences)
- Diagrams stand alone without explanation
- Practical — viewers can follow along with the GitHub repo
- Weekly cadence (AI Agents Masterclass drops weekly)

### Nate Herk's Format

**Channel:** [@NateHerk](https://www.youtube.com/@NateHerk) — AI Automation
**Style:** Screen recording + face cam, shorter format (8-10 min), workflow-focused
**Community:** Runs Skool community "AI Automation Society"

**His approach:**
1. **WAT Framework** — his teaching structure: **W**hat the automation does, **A**rchitecture diagram, **T**echnical walkthrough
2. **Claude Code + n8n** — his niche is connecting Claude Code to automation platforms
3. **Quick wins** — "From Zero to Your First Agentic AI Workflow in 26 Minutes"
4. **Monetization angle** — "5 AI workflows that actually sell in 2026"

**What makes his videos work:**
- Compressed value — 10 minutes, one complete workflow
- Business framing — not "cool tech" but "this makes money"
- Community flywheel — YouTube → Skool → paid tier

### Other Top Creators (from the rankings)

| Creator | Strength | View range |
|---|---|---|
| **Net Ninja** | Best beginner tutorials (2-part, 22 min total) | #1 ranked |
| **Edmund Yong** | "800+ Hours with Claude Code" — experience-based credibility | High engagement |
| **Sabrina** | "5 INSANE Claude Code + Video Prompts" — hook-driven titles | Viral format |
| **Zara Zhang** | Vibe-coded slides — "code makes better slides than PPT" | Twitter-viral |

---

## Visual Tools for Your Presentation

### Option 1: Excalidraw (Cole Medin style) ← RECOMMENDED

**Why:** Hand-drawn aesthetic is THE look for dev YouTube in 2026. Feels authentic, not corporate. Cole Medin literally built a Claude Code skill for this.

**Setup:**
```bash
# Install Cole's Excalidraw skill
cd /home/dima/dev/vexa-agentic-runtime
mkdir -p .claude/skills/excalidraw-diagram
git clone https://github.com/coleam00/excalidraw-diagram-skill.git /tmp/excalidraw-skill
cp -r /tmp/excalidraw-skill/* .claude/skills/excalidraw-diagram/

# Install renderer
cd .claude/skills/excalidraw-diagram/references
uv sync
uv run playwright install chromium
```

**Then ask Claude Code:**
> "Create an Excalidraw diagram showing the three components of AI coding: Machine, CLI Agent, and Files — with arrows showing the data flow"

The agent will generate `.excalidraw` JSON → render to PNG → self-validate → iterate.

**Workflow for the presentation:**
1. Write each slide's concept in the narration script (already done above)
2. Ask Claude Code to generate an Excalidraw diagram for each slide
3. Export as PNG or use Excalidraw's presentation mode
4. Record screen with the diagrams visible

### Option 2: ppt-skills (hand-drawn PPTX) — for PowerPoint workflow

**Repo:** [danny0926/ppt-skills](https://github.com/danny0926/ppt-skills)

A Claude Code skill that generates hand-drawn-style PowerPoint slides. 6-phase pipeline:
1. Content structuring
2. Style selection (6 presets: Clean Sketch, Bold Marker, Notebook, Blackboard, Blueprint, Watercolor)
3. Layout planning (15+ types: split-visual, comparison, big-number, process-flow, timeline)
4. HTML/CSS generation with rough.js
5. PNG rendering via Playwright
6. Dual-layer PPTX assembly (hand-drawn background + editable text on top)

**Good if:** you want editable PPTX files, or prefer the PowerPoint workflow over Excalidraw.

### Option 3: frontend-slides (Zara Zhang style) — web-based slides

**Repo:** [zarazhangrui/frontend-slides](https://github.com/zarazhangrui/frontend-slides)

Claude interviews you about aesthetics first, generates multiple directions to "show not tell," you pick a direction, then it generates HTML/CSS slides. Code creates better slides than most PPT tools. Web-native, responsive, easily screen-recordable.

---

## Recommended Production Setup for Your Video

Based on what works for the top creators in this space:

### Recording
- **Screen:** OBS or ScreenFlow — capture terminal + Excalidraw diagrams
- **Face cam:** Small overlay (bottom-right), not full talking head
- **Audio:** External mic (Blue Yeti / Rode) — audio quality matters more than video

### Structure (12-15 min sweet spot)
```
0:00 - 0:30   Hook          "Everyone's coding on Claude Code. But..."
0:30 - 2:00   Context       3 components, agent loop
2:00 - 4:00   Insight       Not just code — LLMs are general text processors
4:00 - 5:30   Plot twist    4th component: scheduler. Agents that live.
5:30 - 7:00   Problems      Security, scalability, over-privilege
7:00 - 9:00   Solution      3-tier API overview
9:00 - 12:00  Demo          Live API calls — spawn container, chat, bot joins meeting
12:00 - 13:00 Closing       The loop, open source CTA
```

### The Hook Formula (from top-performing videos)
- **Pattern:** "Everyone is doing X. Here's the problem nobody talks about."
- **Thumbnail:** Bold text + 1-2 visual elements, high contrast
- **Title options:**
  - "I Built an Agent Runtime. Here's Why Claude Code Alone Isn't Enough."
  - "From Claude Code to Production: Making AI Agents Actually Scalable"
  - "The 4th Component Every AI Agent Needs (and Nobody's Building)"

### Sources

- [Cole Medin's Excalidraw Diagram Skill](https://github.com/coleam00/excalidraw-diagram-skill)
- [Best Claude Code YouTube Videos Ranked — 2026 Guide](https://medium.com/@rentierdigital/i-watched-25-claude-code-youtube-videos-so-you-dont-have-to-the-definitive-ranking-550aa6863840)
- [Build Beautiful Diagrams with Claude Code](https://www.geeky-gadgets.com/claude-diagram-render-validation/)
- [ppt-skills — Hand-drawn PPTX Generator](https://github.com/danny0926/ppt-skills)
- [frontend-slides — Web-based Slide Generator](https://github.com/zarazhangrui/frontend-slides)
- [Nate Herk — AI Automation Society](https://www.skool.com/ai-automation-society)
- [Cole Medin on GOTO Tech](https://gotopia.tech/experts/2150/cole-medin)
- [Nate Herk — From Zero to Agentic AI Workflow](https://www.classcentral.com/course/youtube-from-zero-to-your-first-agentic-ai-workflow-in-26-minutes-claude-code-531313)
