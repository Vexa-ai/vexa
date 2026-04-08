<p align="center" style="margin-bottom: 0.75em;">
  <img src="assets/logodark.svg" alt="Vexa Logo" width="56"/>
</p>

<h1 align="center" style="margin-top: 0.25em; margin-bottom: 0.5em; font-size: 2.5em; font-weight: 700; letter-spacing: -0.02em;">Vexa</h1>

<p align="center" style="font-size: 1.75em; margin-top: 0.5em; margin-bottom: 0.75em; font-weight: 700; line-height: 1.3; letter-spacing: -0.01em;">
  <strong>Open-source meeting transcription API & agent runtime</strong>
</p>

<p align="center" style="font-size: 1em; color: #a0a0a0; margin-top: 0.5em; margin-bottom: 1.5em; letter-spacing: 0.01em;">
  meeting bots • real-time transcription • interactive agents • MCP server • self-hosted
</p>

<p align="center" style="margin: 1.5em 0; font-size: 1em;">
  <img height="24" src="assets/google-meet.svg" alt="Google Meet" style="vertical-align: middle; margin-right: 10px;"/> <strong style="font-size: 1em; font-weight: 600;">Google Meet</strong>
  &nbsp;&nbsp;&nbsp;&nbsp;•&nbsp;&nbsp;&nbsp;&nbsp;
  <img height="24" src="assets/microsoft-teams.svg" alt="Microsoft Teams" style="vertical-align: middle; margin-right: 10px;"/> <strong style="font-size: 1em; font-weight: 600;">Microsoft Teams</strong>
  &nbsp;&nbsp;&nbsp;&nbsp;•&nbsp;&nbsp;&nbsp;&nbsp;
  <img height="24" src="assets/icons8-zoom.svg" alt="Zoom" style="vertical-align: middle; margin-right: 10px;"/> <strong style="font-size: 1em; font-weight: 600;">Zoom</strong>
</p>

<p align="center" style="margin: 1.75em 0 1.25em 0;">
  <a href="https://github.com/Vexa-ai/vexa/stargazers"><img src="https://img.shields.io/github/stars/Vexa-ai/vexa?style=flat-square&color=yellow" alt="Stars"/></a>
  &nbsp;&nbsp;&nbsp;
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square" alt="License"/></a>
  &nbsp;&nbsp;&nbsp;
  <a href="https://discord.gg/Ga9duGkVz9"><img src="https://img.shields.io/badge/Discord-join-5865F2?style=flat-square&logo=discord&logoColor=white" alt="Discord"/></a>
</p>

<p align="center">
  <a href="#meeting-api">Meeting API</a> •
  <a href="#agent-api">Agent API</a> •
  <a href="#runtime-api">Runtime API</a> •
  <a href="#quickstart">Quickstart</a> •
  <a href="https://docs.vexa.ai">Docs</a> •
  <a href="#roadmap">Roadmap</a> •
  <a href="https://discord.gg/Ga9duGkVz9">Discord</a>
</p>

---

## What is Vexa?

**Vexa** is an open-source, self-hostable meeting transcription API and agent runtime for Google Meet, Microsoft Teams, and Zoom. Modular by design — twelve independent features, use one or all:

- **Meeting transcription API** — send a bot to any meeting, get real-time transcripts via REST API and WebSocket. Self-hosted alternative to Otter.ai, Fireflies.ai.
- **Meeting bot API** — auto-join, record, speak, chat, share screen. Open-source alternative to Recall.ai.
- **Agent runtime** — ephemeral containers for AI agents with zero idle cost. Post-meeting automation, scheduled pipelines.
- **MCP server** — 17 meeting tools for Claude, Cursor, Windsurf. Agents join calls, read transcripts, speak in meetings.

Every feature is a separate service. Pick what you need, skip what you don't. Self-host everything or use [vexa.ai](https://vexa.ai) hosted.

### At a glance

| Capability | What it means |
|---|---|
| **Meeting bots** | Automatically joins Google Meet, Microsoft Teams, and Zoom meetings |
| **Real-time transcription** | Sub-second transcript delivery during the call |
| **Interactive bots** | Make bots speak, send/read chat, share screen content, and set avatar in live meetings |
| **Agent runtime** | Ephemeral containers for AI agents — browser, agent, worker profiles. Zero cost when idle |
| **Post-meeting automation** | Meetings trigger action: summarize, create tickets, push to Slack/CRM — no human needed |
| **Proactive agents** | Scheduler + calendar integration: agents join meetings on schedule, act without being asked |
| **MCP server** | 17 tools for Claude/Cursor/Windsurf — join calls, read transcripts, speak in meetings |
| **Multilingual** | 100+ languages via Whisper (transcription + translation) |
| **Multi-tenant** | Users, scoped API tokens, isolated containers — deploy once, serve your team |
| **Self-hostable** | Run on your infra for complete data sovereignty |
| **Accessible anywhere** | Same agent via web dashboard, Telegram, meeting chat, or any chat client |

### Who it's for

| You are... | You want... |
|---|---|
| **Enterprises** | Self-hosted transcription with strict privacy — replace $17-20/seat SaaS |
| **AI product builders** | Give your agents meeting superpowers via MCP or API |
| **SaaS developers** | Multi-tenant meeting API to embed in your product |
| **Platform teams** | Deploy one service for your org — isolated agents, scoped tokens, no per-user infra |
| **Automation builders** | Post-meeting pipelines: meeting ends → agent summarizes → Slack/CRM/Linear |

---

## Build on Top. In Hours, Not Months

**Build powerful meeting assistants (like Otter.ai, Fireflies.ai, Fathom) for your startup, internal use, or custom integrations.** Or go further — build AI agents that don't just transcribe, but actively participate in and act on meetings.

The Vexa API provides powerful abstractions and a clear separation of concerns, enabling you to build sophisticated applications on top with a safe and enjoyable coding experience.

## 🛡️ Built for Data Sovereignty

Vexa is open-source and self-hostable — ideal for regulated industries and teams that cannot compromise on privacy. 

Modular architecture scales from edge devices to millions of users. You choose what to self-host and what to use as a service.

**You control everything:**

**1. Hosted service**
At [vexa.ai](https://vexa.ai) — get an API key and start sending bots. No infrastructure needed.
*<small style="color: #999;">Ready to integrate</small>*

<hr style="margin: 1.25em 0; border: none; border-top: 1px solid #333;">

**2. Self-host with Vexa transcription**
Run Vexa yourself, use vexa.ai for transcription — ready to go, no GPU needed.
*<small style="color: #999;">Control with minimal DevOps</small>*

<hr style="margin: 1.25em 0; border: none; border-top: 1px solid #333;">

**3. Fully self-host**
Run everything including your own GPU transcription service.
*<small style="color: #999;">Full data sovereignty for regulated industries</small>*


<a id="whats-new"></a>

## What's new

**Agent API (preview)**
- **Vexa CLI** — `vexa` replaces `claude` CLI, routes through ephemeral containers with persistent workspaces ([install](#vexa-cli))
- **Ephemeral agent containers** — browser, agent, worker profiles with zero idle cost
- **Agent API** — agent sessions, Claude CLI streaming, workspace sync, scheduling
- **Scheduler** — cron, relative delays, `on_success`/`on_failure` container chaining
- **Remote browser** — VNC + CDP, persistent auth across sessions
- **Telegram bot** — interact with your agent from mobile
- **Dashboard agent chat** — talk to your agent from the web UI

**v0.9 (pre-release)**
- **Zoom:** initial Zoom Meeting SDK support (requires Zoom app setup/approval; see docs)
- **Recordings:** persist recording artifacts to S3-compatible storage (or local)
- **Post-meeting playback:** stream recordings via `/recordings/.../raw` with `Range` seeking (`206`) + `Content-Disposition: inline`
- **Delete semantics:** deleting a meeting also purges recording objects/artifacts (best-effort) before anonymizing the meeting
- **Interactive Bots API:** live controls for speak/chat/screen/avatar during active meetings
- **MCP server:** 17 tools for AI agents — join, transcribe, speak, chat, share screen

---

> See full release notes: https://github.com/Vexa-ai/vexa/releases

---

## Quickstart

### Option 1: Hosted (no deployment needed)

Get your API key at [vexa.ai/dashboard/api-keys](https://vexa.ai/dashboard/api-keys) and start sending bots immediately. No infrastructure needed.

### Option 2: Vexa Lite (recommended for self-hosting)

**Single Docker container. Easiest way to self-host Vexa.**

- **Self-hosted multiuser service** - Multiple users, API tokens, and team management
- **Single container** - Easy to deploy on any platform
- **No GPU required** - Transcription runs externally
- **Choose your frontend** - Pick from open-source user interfaces like [Vexa Dashboard](./services/dashboard)
- **Production-ready** - Stateless, scalable, serverless-friendly

Needs external Postgres + transcription service. Use Vexa transcription (sign up at [vexa.ai](https://vexa.ai) for a transcription API key — ready to go, no GPU needed), or self-host your own GPU transcription for full data sovereignty.

**Quick start:**
```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://user:pass@host/vexa" \
  -e ADMIN_API_TOKEN="your-admin-token" \
  -e TRANSCRIPTION_SERVICE_URL="https://transcription.service/v1/audio/transcriptions" \
  -e TRANSCRIPTION_SERVICE_TOKEN="transcriber-token" \
  vexaai/vexa-lite:latest
```

> **Production:** Use immutable tags (e.g., `0.10.0-260405-0108`) instead of `:latest` for reproducible deployments.

**Deployment options:**
- **One-click platform deployments**: [vexa-lite-deploy repository](https://github.com/Vexa-ai/vexa-lite-deploy) (Fly.io ready, more platforms coming)
- **Complete setup guide**: [Vexa Lite Deployment Guide](https://docs.vexa.ai/vexa-lite-deployment)
- **Frontend options**: [Vexa Dashboard](./services/dashboard)

### Option 3: Docker Compose (development)

**Full stack deployment with all services. Perfect for development and testing.**

```bash
git clone https://github.com/Vexa-ai/vexa.git
cd vexa/deploy/compose
make all
```

**What `make all` does:**
- Builds all Docker images
- Spins up all containers (API, bots, transcription services, database)
- Runs database migrations
- Starts a simple test to verify everything works

Full guide: [Deployment Guide](https://docs.vexa.ai/deployment)

### Option 4: Helm (production K8s)

For Kubernetes production deployments. See [deploy/helm/README.md](deploy/helm/README.md).

<a id="vexa-cli"></a>

### Vexa CLI

Local terminal client for the agent runtime. Works like `claude` but routes through ephemeral containers with persistent workspaces.

```bash
# Install
pip install "git+https://github.com/Vexa-ai/vexa.git#subdirectory=packages/vexa-cli"

# Configure
vexa config

# Chat (one-shot)
vexa -p "what files are in my workspace?"

# Chat (interactive REPL)
vexa

# Forward any claude CLI flags
vexa -p "review this" --flags "--effort high --permission-mode auto"
```

Sessions persist across container restarts. Workspace files sync to S3. See [packages/vexa-cli/README.md](packages/vexa-cli/README.md) for full docs.

### Recording storage (local and cloud)

Recording supports local filesystem, MinIO, and cloud S3-compatible backends.
See [Recording Storage](https://docs.vexa.ai/recording-storage) for configuration details.

<a id="agent-api"></a>

## Agent API

Vexa isn't just a meeting API — it's a runtime where AI agents natively understand meetings. No gluing together E2B sandboxes with Recall.ai webhooks. Meetings are a built-in primitive.

### What agents can do

| Capability | How |
|-----------|-----|
| **Join meetings** | `vexa meeting join --platform teams --url {url}` |
| **Transcribe live** | `vexa meeting transcript {id}` — streams segments in real-time |
| **Speak in meetings** | `vexa meeting speak --text "Here's the summary"` — TTS to meeting audio |
| **Read/write chat** | `vexa meeting chat --text "Action items..."` — interact via meeting chat |
| **Share screen** | `vexa meeting screen --type url --url {url}` — display content to participants |
| **Control browsers** | `vexa container spawn --profile browser` + CDP/Playwright automation |
| **Schedule pipelines** | `vexa schedule --cron "0 9 * * 1-5" chat "join standup, send notes"` |
| **Persist memory** | `vexa workspace save` — workspace survives across sessions |

### Specialist containers, not fat stacks

| Profile | RAM | Purpose | Idle cost |
|---------|-----|---------|-----------|
| **browser** | ~1.5GB | Meeting attendance, authenticated sessions, VNC + CDP | Zero — killed on timeout |
| **agent** | ~200MB | Claude CLI, summarization, automation, chat | Zero — killed on completion |
| **worker** | ~50MB | Webhook delivery, file processing, notifications | Zero — fire and forget |

The browser doesn't carry an agent. The agent doesn't carry Chromium. They connect via CDP over the network when needed, then die independently.

### Agents that act without being asked

```
Every weekday at 9am:
  → Browser container spawns, joins standup
  → Transcribes the meeting live
  → Meeting ends → agent container wakes up
  → Summarizes, creates Linear tickets, posts to #engineering
  → All containers die. Zero cost until tomorrow.
```

No human triggered anything. Calendar → scheduler → containers → business action.

### Multi-tenant by design

Unlike single-user agent tools (OpenClaw: "not a hostile multi-tenant security boundary," one VPS per user), Vexa runs as a multi-user service:

- **Isolated containers** per user, per session
- **Scoped API tokens** — `bot`, `tx`, `admin` enforced at gateway
- **User/team management** via Admin API
- **Deploy once**, serve your whole team — or your customer base

### MCP server — 17 meeting tools for AI agents

Connect Claude, Cursor, Windsurf, or any MCP client. Your agent gains meeting superpowers:

```
Tools: join_meeting, list_meetings, get_transcript, search_meetings,
       bot_speak, send_chat, read_chat, share_screen, set_avatar,
       start_recording, stop_recording, get_recording, list_bots,
       create_bot, stop_bot, get_bot_status, schedule_meeting
```

Other meeting MCP servers (Otter, Fireflies, Read.ai) are read-only. Vexa's is read + write + control — and self-hosted.

### Knowledge that builds itself from meetings

Every meeting makes your agent smarter. The [workspace knowledge template](./features/agentic-runtime/workspaces/) gives agents a file-based knowledge OS:

```
timeline.md          — logarithmic self-journal (past, present, future)
soul.md              — agent's understanding of you (what works, what doesn't)
streams/             — active working topics (flat .md files, wiki-linked)
notes.md             — inbox/scratchpad
knowledge/
  entities/
    contacts/        — people profiles, auto-extracted from meetings
    companies/       — org profiles
    products/        — product/project profiles
  meetings/          — meeting minutes, linked to entities
  action-items/      — tracked per meeting
scripts/             — automation scripts, scheduled via `vexa schedule`
```

After a meeting ends, the agent wakes up, reads the transcript, extracts entities into `knowledge/entities/`, creates meeting minutes with `[[wiki-links]]`, tracks action items, and updates the timeline. Like Obsidian + a CRM + a meeting assistant — but the agent does the work.

**How this compares:**

| Platform | Memory model | Meetings feed it? | Entity graph | Self-hosted |
|----------|-------------|-------------------|-------------|-------------|
| **OpenClaw** | MEMORY.md + SOUL.md (flat files) | No | No | Yes |
| **Mem0** | Vector + graph + KV (48K stars) | No | API-based | No (SaaS) |
| **Obsidian + AI** | Markdown + wiki-links | Manual | Manual | Yes (local) |
| **Clay/Attio** | CRM with AI enrichment | Via integrations | Yes | No |
| **Vexa Workspaces** | Markdown + wiki-links + entities + streams + timeline + soul | **Yes — automatic** | **Yes — contacts, companies, products** | **Yes** |

### Your agent, wherever you are

Same agent, same memory, every surface:

| Surface | Status |
|---------|--------|
| **Web dashboard** | Working — chat with your agent, see meetings, play recordings |
| **Telegram** | Working — message your agent from your phone |
| **Meeting chat** | Working — agent reads/responds in live meeting chat |
| **Slack/Discord** | Planned — same Agent API backbone |

---

<a id="meeting-api"></a>

## Meeting API — The Data Layer

The Meeting API is the foundation of the platform. It sends bots to meetings, captures real-time transcripts with per-speaker audio, and provides interactive controls (speak, chat, share screen). This is the data layer that feeds everything else — agents, webhooks, knowledge extraction.

### 1. Send bot to meeting:

Set `API_BASE` to your deployment:

- Hosted: `https://api.cloud.vexa.ai`
- Self-hosted Lite: `http://localhost:8056`
- Self-hosted full stack (default): `http://localhost:8056`

```bash
export API_BASE="http://localhost:8056"
```

### Request a bot for Microsoft Teams

```bash
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{
    "platform": "teams",
    "native_meeting_id": "<NUMERIC_MEETING_ID>",
    "passcode": "<MEETING_PASSCODE>"
  }'
```

### Or request a bot for Google Meet

```bash
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij"
  }'
```

### Or request a bot for Zoom

```bash
# Caveat: Zoom Meeting SDK apps typically require Marketplace approval to join other users' meetings.
# Before approval, expect you can reliably join only meetings created by you (the authorizing account).
#
# From URL: https://us05web.zoom.us/j/YOUR_MEETING_ID?pwd=YOUR_PWD
# Extract meeting ID and optional passcode separately.
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{
    "platform": "zoom",
    "native_meeting_id": "YOUR_MEETING_ID",
    "passcode": "YOUR_PWD",
    "recording_enabled": true,
    "transcribe_enabled": true,
    "transcription_tier": "realtime"
  }'
```

### 2. Get transcripts:

### Get transcripts over REST

```bash
curl -H "X-API-Key: <API_KEY>" \
  "$API_BASE/transcripts/<platform>/<native_meeting_id>"
```

For real-time streaming (sub‑second), see the [WebSocket guide](https://docs.vexa.ai/websocket).
For full REST details, see the [User API Guide](https://docs.vexa.ai/user_api_guide).

Note: Meeting IDs are user-provided (Google Meet code like `xxx-xxxx-xxx` or Teams numeric ID and passcode). Vexa does not generate meeting IDs.

---

## Who Vexa is for

* **Enterprises (self-host):** Replace $17-20/seat meeting SaaS — data sovereignty, no per-seat fees
* **AI product builders:** Give your agents meeting superpowers via MCP — join calls, speak, act on transcripts
* **SaaS developers:** Multi-tenant meeting API to embed in your product — scoped tokens, isolated containers
* **Platform/DevOps teams:** Deploy once for your org — proactive agents, scheduled meetings, zero babysitting
* **Automation builders:** Post-meeting pipelines with n8n, webhooks, and agent containers
  - Tutorial: https://vexa.ai/blog/google-meet-transcription-n8n-workflow

---

## Roadmap

For the up-to-date roadmap and priorities, see GitHub Issues and Milestones. Issues are grouped by milestones to show what's coming next, in what order, and what's currently highest priority.

- Issues: https://github.com/Vexa-ai/vexa/issues
- Milestones: https://github.com/Vexa-ai/vexa/milestones

> For discussion/support, join our [Discord](https://discord.gg/Ga9duGkVz9).

## Architecture

**Core API services** (always running):

| Service | Purpose |
|---------|---------|
| [api-gateway](./services/api-gateway) | Reverse proxy — routes REST, WebSocket, VNC, CDP to backends |
| [admin-api](./services/admin-api) | User/org CRUD, scoped API tokens, team management |
| [meeting-api](./services/meeting-api) | **Data layer** — bot lifecycle, meeting CRUD, recordings, transcription collector, interactive bot controls |
| [agent-api](./services/agent-api) | **Intelligence layer** — agent sessions, Claude CLI streaming, workspace sync, scheduling |
| [runtime-api](./services/runtime-api) | **Infrastructure layer** — container CRUD, spawn/stop/exec, port mapping, idle timeout. Docker, Kubernetes, or process backend |

**Meeting & AI services:**

| Service | Purpose |
|---------|---------|
| [vexa-bot](./services/vexa-bot) | Joins meetings, captures per-speaker audio, transcribes, interactive controls |
| [transcription-service](./services/transcription-service) | GPU inference — OpenAI-compatible Whisper API |
| [tts-service](./services/tts-service) | Text-to-speech for bot voice |
| [mcp](./services/mcp) | 17-tool MCP server for AI agents (Claude, Cursor, etc.) |

**Frontends & clients:**

| Service | Purpose |
|---------|---------|
| [dashboard](./services/dashboard) | Open-source Next.js web UI — meetings, transcripts, agent chat, browser sessions |

**Ephemeral containers** (spawned on demand, auto-reclaimed):

| Profile | RAM | Use case |
|---------|-----|----------|
| **browser** | ~1.5GB | Meeting attendance, authenticated browser sessions (VNC + CDP) |
| **agent** | ~200MB | Claude CLI, post-meeting processing, automation |
| **worker** | ~50MB | Webhook delivery, file processing |

- Database models: `libs/admin-models/` (users, tokens), `services/meeting-api/` (meetings, transcriptions)

<a id="runtime-api"></a>

### Runtime API — The Infrastructure Layer

The [Runtime API](./services/runtime-api) abstracts container lifecycle behind a single REST API. It's what allows Meeting API to spawn bot containers and Agent API to spawn agent sandboxes — without either caring whether the backend is Docker, Kubernetes, or bare processes.

`POST /containers` with a profile name → get a managed container back. It idles out automatically, fires a callback when it exits, and enforces per-user limits. Switch from Docker in dev to Kubernetes in prod by changing one env var.

> 💫 If you're building with Vexa, we'd love your support! [Star our repo](https://github.com/Vexa-ai/vexa/stargazers) to help us reach 2000 stars.

### Features

**Meeting transcription:**
- **Real-time multilingual transcription** supporting **100 languages** with **Whisper** — replace $17-20/seat SaaS
- **Post-meeting transcription** — record during meeting, transcribe on demand with full-audio context
- **Per-speaker audio** — no diarization needed, speaker labels from the platform itself
- **WebSocket streaming** — sub-second transcript delivery via WebSocket
- **Google Meet, Microsoft Teams, Zoom** — one API, all platforms, auto-detected from URL

**Interactive bots:**
- **Speaking bot** — TTS voice in meetings (like Recall.ai's Output Media API, but open-source)
- **Chat** — read/write meeting chat for AI-powered in-meeting interaction
- **Screen sharing** — display content to meeting participants programmatically
- **Avatar** — set bot avatar/display name per meeting

**Agent runtime:**
- **Ephemeral containers** — browser/agent/worker profiles, zero idle cost, ~5s spin-up
- **System layer fluency** — agents wake up knowing Vexa (CLI + instructions baked into image)
- **Knowledge workspaces** — file-based knowledge OS: entities, streams, timeline, wiki-links — auto-populated from meetings
- **Container chaining** — `on_success`/`on_failure` callbacks orchestrate multi-step pipelines
- **Scheduler** — cron + relative delays + event-triggered jobs, backed by Redis sorted sets
- **Remote browser** — VNC for human control + CDP for agent automation, persistent auth

**Platform:**
- **Multi-tenant** — users, orgs, scoped API tokens, container isolation
- **MCP server** — 17 tools for Claude, Cursor, Windsurf — read + write + control meetings
- **Webhooks** — push events for post-meeting automation pipelines
- **REST API** — complete API for bots, users, transcripts, recordings, agents
- **Self-hostable** — full data sovereignty, Apache-2.0 licensed
- **Open-source frontends** — [Vexa Dashboard](./services/dashboard), Telegram bot

**Deployment & Management Guides:**
- [Vexa Lite Deployment Guide](https://docs.vexa.ai/vexa-lite-deployment) - Single container deployment
- [Docker Compose Deployment](https://docs.vexa.ai/deployment) - Full stack for development
- [Self-Hosted Management Guide](https://docs.vexa.ai/self-hosted-management) - Managing users and API tokens
- [Recording Storage](https://docs.vexa.ai/recording-storage) - S3, MinIO, and local storage configuration

## Modular — Pick What You Need

Vexa is a toolkit, not a monolith. Every feature works independently. Use one or all twelve — they compose when you need them to.

| You're building... | Features you need | Skip the rest |
|-------------------|------------------|---------------|
| **Self-hosted Otter replacement** | transcription + multi-platform + webhooks | agent runtime, scheduler, MCP |
| **Meeting data pipeline** | transcription + webhooks + post-meeting | speaking-bot, chat, agent runtime |
| **AI meeting assistant product** | transcription + MCP + speaking-bot + chat | remote-browser, scheduler |
| **Proactive meeting agent** | scheduler + calendar + agentic-runtime + transcription | MCP, token-scoping |
| **Personal AI assistant** | agentic-runtime + workspaces + scheduler + Telegram | multi-platform, webhooks |
| **Meeting bot API (like Recall.ai)** | multi-platform + transcription + token-scoping | agent runtime, workspaces |

You don't pay complexity tax for features you don't use. Each service is a separate container. Don't need agents? Don't run agent-api. Don't need TTS? Don't run tts-service. The architecture is modular by design — services communicate via REST and Redis, not tight coupling.

## Features — Honest Status

Each feature has its own README with business context, architecture, DoD table, and confidence score. **Confidence scores are evidence-based** — calculated from DoD pass/fail items and tests3 checks. We update these continuously.

| Feature | Confidence | Status |
|---------|-----------|--------|
| [meeting-urls](./features/meeting-urls/) | 100 | All 9 checks PASS. Teams e2e validated. |
| [browser-session](./features/browser-session/) | 95 | 18/18 DoD PASS. Google login persistence validated. |
| [infrastructure](./features/infrastructure/) | 95 | All items pass including build. |
| [bot-lifecycle](./features/bot-lifecycle/) | 92 | 12/14 PASS. Unauthenticated join + escalation untested. |
| [container-lifecycle](./features/container-lifecycle/) | 90 | 14/15 PASS. K8s profiles untested. |
| [webhooks](./features/webhooks/) | 90 | All items pass. Live delivery not yet verified. |
| [remote-browser](./features/remote-browser/) | 90 | CDP, S3, idle timeout validated. Zombie processes remain. |
| [dashboard](./features/dashboard/) | 90 | Login, proxy, pagination, transcript all tested via tests3. |
| [meeting-chat](./features/meeting-chat/) | 80 | All items pass. No multi-message e2e flow tested. |
| [authenticated-meetings](./features/authenticated-meetings/) | 75 | 8/10 PASS. Fallback FAIL, Teams auth not implemented. |
| [speaking-bot](./features/speaking-bot/) | 70 | Ceiling items pass. E2E TTS in live meeting works. |
| [auth-and-limits](./features/auth-and-limits/) | 70 | Ceiling items pass. Rate limiting + token CRUD untested. |
| [realtime-transcription](./features/realtime-transcription/) | 65 | GMeet + Teams work. Zoom not implemented. |
| [post-meeting-transcription](./features/post-meeting-transcription/) | 20 | Recording upload broken both platforms. |

### Services

| Service | Confidence | Status |
|---------|-----------|--------|
| [agent-api](./services/agent-api/) | 90 | 32 checks, CLI commands validated. |
| [vexa-bot](./services/vexa-bot/) | 80 | All items covered via feature tests. Zoom untested. |
| [dashboard](./services/dashboard/) | 75 | 5/6 items via tests3 checks. npm test not run. |
| [api-gateway](./services/api-gateway/) | 68 | Core routing, auth, WebSocket validated. |
| [admin-api](./services/admin-api/) | 62 | User/token CRUD, schema sync tested. |
| [transcription-service](./services/transcription-service/) | 55 | Health + transcription + auth pass. Backpressure untested. |
| [runtime-api](./services/runtime-api/) | 52 | Container CRUD, idle, callbacks tested. |
| [meeting-api](./services/meeting-api/) | 52 | Bot create, status, URL parsing. Tech debt remains. |
| [tts-service](./services/tts-service/) | 30 | Indirect only — works via speaking-bot feature. |
| [mcp](./services/mcp/) | 0 | Untested. |
| [calendar-service](./services/calendar-service/) | 0 | Experimental, not in default compose. |
| [telegram-bot](./services/telegram-bot/) | 0 | No bot token configured in dev/CI. |

### Deployments

| Mode | Confidence | Status |
|------|-----------|--------|
| [compose](./deploy/compose/) | 93 | 18/19 DoD PASS. Transcription not tested from inside containers. |
| [lite](./deploy/lite/) | 80 | 9/9 DoD PASS. Zombie reaper bug caps reliability. |
| [helm](./deploy/helm/) | 75 | vexa chart validated on LKE. vexa-lite chart not tested. |

## Related Projects

Vexa is part of an ecosystem of open-source tools:

### [Vexa Dashboard](./services/dashboard)
100% open-source web interface for Vexa, included in this monorepo at `services/dashboard/`. Join meetings, view transcripts, chat with agents, manage browser sessions, and more. Self-host everything with no cloud dependencies.

## Contributing

We use **GitHub Issues** as our main feedback channel. New issues are triaged within **72 hours** (you'll get a label + short response). Not every feature will be implemented, but every issue will be acknowledged. Look for **`good-first-issue`** if you want to contribute.

Contributors are welcome! Join our community and help shape Vexa's future. Here's how to get involved:

1. **Understand Our Direction**:
2. **Engage on Discord** ([Discord Community](https://discord.gg/Ga9duGkVz9)):

   * **Introduce Yourself**: Start by saying hello in the introductions channel.
   * **Stay Informed**: Check the Discord channel for known issues, feature requests, and ongoing discussions. Issues actively being discussed often have dedicated channels.
   * **Discuss Ideas**: Share your feature requests, report bugs, and participate in conversations about a specific issue you're interested in delivering.
   * **Get Assigned**: If you feel ready to contribute, discuss the issue you'd like to work on and ask to get assigned on Discord.
3. **Development Process**:

   * Browse available **tasks** (often linked from Discord discussions or the roadmap).
   * Request task assignment through Discord if not already assigned.
   * Submit **pull requests** for review.

- **Critical Tasks & Bounties**:
  - Selected **high-priority tasks** may be marked with **bounties**.
  - Bounties are sponsored by the **Vexa core team**.
  - Check task descriptions (often on the roadmap or Discord) for bounty details and requirements.

We look forward to your contributions!

Licensed under **Apache-2.0** — see [LICENSE](LICENSE).

## Project Links

- 🌐 [Vexa Website](https://vexa.ai)
- 💼 [LinkedIn](https://www.linkedin.com/company/vexa-ai/)
- 🐦 [X (@grankin_d)](https://x.com/grankin_d)
- 💬 [Discord Community](https://discord.gg/Ga9duGkVz9)

## Repository Structure

This is the main Vexa repository containing the core API and services. For related projects:

- **[vexa-lite-deploy](https://github.com/Vexa-ai/vexa-lite-deploy)** - Deployment configurations for Vexa Lite
- **[Vexa Dashboard](./services/dashboard)** - Web UI for managing Vexa instances (included in this monorepo)

[![Meet Founder](https://img.shields.io/badge/LinkedIn-Dmitry_Grankin-0A66C2?style=flat-square&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/dmitry-grankin/)

[![Join Discord](https://img.shields.io/badge/Discord-Community-5865F2?style=flat-square&logo=discord&logoColor=white)](https://discord.gg/Ga9duGkVz9)

The Vexa name and logo are trademarks of **Vexa.ai Inc**.
