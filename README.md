

# **Vexa**

**Open-source meeting transcription API & agent runtime**

meeting bots • real-time transcription • interactive agents • MCP server • self-hosted

**Google Meet**     •     **Microsoft Teams**     •     **Zoom** (experimental)

       

[Meeting API](#meeting-api) • [Quickstart](#quickstart) • [Docs](https://docs.vexa.ai) • [Roadmap](#roadmap) • [Discord](https://discord.gg/Ga9duGkVz9)

---

## What is Vexa?

**Vexa** is an open-source, self-hostable meeting transcription API and agent runtime for Google Meet, Microsoft Teams, and Zoom. Modular by design — twelve independent features, use one or all:

- **Meeting transcription API** — send a bot to any meeting, get real-time transcripts via REST API and WebSocket. Self-hosted alternative to Otter.ai, Fireflies.ai.
- **Meeting bot API** — auto-join, record, speak, chat, share screen. Open-source alternative to Recall.ai.
- **Agent runtime** — ephemeral containers for AI agents with zero idle cost. Post-meeting automation, scheduled pipelines.
- **MCP server** — 17 meeting tools for Claude, Cursor, Windsurf. Agents join calls, read transcripts, speak in meetings.

Every feature is a separate service. Pick what you need, skip what you don't. Self-host everything or use [vexa.ai](https://vexa.ai) hosted.

### At a glance



| Capability                          | What it means                                                                               |
| ----------------------------------- | ------------------------------------------------------------------------------------------- |
| **Meeting bots**                    | Automatically joins Google Meet, Microsoft Teams, and Zoom meetings                         |
| **Browser bots**                    | CDP + Playwright browser automation with persistent authenticated sessions via S3           |
| **Real-time transcription**         | Sub-second transcript delivery during the call                                              |
| **Interactive bots**                | Make bots speak, send/read chat, share screen content, and set avatar in live meetings      |
| **Agent runtime** *(experimental)*  | Ephemeral containers for AI agents — browser, agent, worker profiles. Zero cost when idle   |
| **MCP server**                      | 17 tools for Claude/Cursor/Windsurf — join calls, read transcripts, speak in meetings       |
| **Multilingual**                    | 100+ languages via Whisper (transcription + translation)                                    |
| **Multi-tenant**                    | Users, scoped API tokens, isolated containers — deploy once, serve your team                |
| **Self-hostable**                   | Run on your infra for complete data sovereignty                                             |
| **Accessible anywhere**             | Same agent via web dashboard, meeting chat, or any chat client                              |


### Who it's for


| You are...              | You want...                                                                         |
| ----------------------- | ----------------------------------------------------------------------------------- |
| **Enterprises**         | Self-hosted transcription with strict privacy — replace $17-20/seat SaaS            |
| **AI product builders** | Give your agents meeting superpowers via MCP or API                                 |
| **SaaS developers**     | Multi-tenant meeting API to embed in your product                                   |
| **Platform teams**      | Deploy one service for your org — isolated agents, scoped tokens, no per-user infra |
| **Automation builders** | Post-meeting pipelines via webhooks: meeting ends → your system processes transcript |


---

## Build on Top. In Hours, Not Months

**Build powerful meeting assistants (like Otter.ai, Fireflies.ai, Fathom) for your startup, internal use, or custom integrations.** Or build a meeting bot API like [Recall.ai](https://recall.ai) — but open-source and self-hosted. Go further — build AI agents that don't just transcribe, but actively participate in and act on meetings.

The Vexa API provides powerful abstractions and a clear separation of concerns, enabling you to build sophisticated applications on top with a safe and enjoyable coding experience.

## 🛡️ Built for Data Sovereignty

Vexa is open-source and self-hostable — ideal for regulated industries and teams that cannot compromise on privacy. 

Modular architecture scales from edge devices to millions of users. You choose what to self-host and what to use as a service.

**You control everything:**

**1. Hosted service**
At [vexa.ai](https://vexa.ai) — get an API key and start sending bots. No infrastructure needed.
*Ready to integrate*

---

**2. Self-host with Vexa transcription**
Run Vexa yourself, use vexa.ai for transcription — ready to go, no GPU needed.
*Control with minimal DevOps* — see [deploy/](./deploy/) for setup guides.

---

**3. Fully self-host**
Run everything including your own GPU transcription service.
*Full data sovereignty for regulated industries* — see [deploy/](./deploy/) for setup guides.

## What's new

**v0.10**

- **Zoom** *(experimental)* — initial Zoom Meeting SDK support. Requires Zoom app setup and Marketplace approval. Before approval, bots can only join meetings created by the authorizing account. Not yet validated in production.
- **Interactive Bots API** — live controls for speak/chat/screen/avatar during active meetings
- **MCP server** — 17 tools for AI agents — join, transcribe, speak, chat, share screen
- **Recordings** — persist recording artifacts to S3-compatible storage (or local)
- **Agent API** *(experimental)* — ephemeral containers, Claude CLI streaming, workspace sync, scheduling. See [Agent API](#agent-api-experimental).

---

> See full release notes: [https://github.com/Vexa-ai/vexa/releases](https://github.com/Vexa-ai/vexa/releases)

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



### Vexa CLI *(experimental)*

Local terminal client for the agent runtime. See [packages/vexa-cli/README.md](packages/vexa-cli/README.md).

### Recording storage (local and cloud)

Recording supports local filesystem, MinIO, and cloud S3-compatible backends.
See [Recording Storage](https://docs.vexa.ai/recording-storage) for configuration details.



## Agent API *(experimental)*

Ephemeral containers for AI agents with meeting-native capabilities. Agents join calls, read transcripts, speak, and act — without gluing together separate sandboxes and meeting APIs.

- **Ephemeral containers** — browser/agent/worker profiles, zero idle cost
- **MCP server** — 17 tools for Claude, Cursor, Windsurf — read + write + control meetings
- **Multi-tenant** — isolated containers, scoped API tokens, user/team management

See [services/agent-api/](./services/agent-api/) for details.

---



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

### Or request a bot for Zoom *(experimental)*

> **Zoom status:** Initial Zoom Meeting SDK support. Requires Zoom app setup and Marketplace approval. Before approval, bots can only join meetings created by the authorizing account. Not yet validated end-to-end in production. Google Meet and Teams are the recommended platforms.

```bash
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{
    "platform": "zoom",
    "native_meeting_id": "YOUR_MEETING_ID",
    "passcode": "YOUR_PWD"
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

---

## Roadmap

For the up-to-date roadmap and priorities, see GitHub Issues and Milestones. Issues are grouped by milestones to show what's coming next, in what order, and what's currently highest priority.

- Issues: [https://github.com/Vexa-ai/vexa/issues](https://github.com/Vexa-ai/vexa/issues)
- Milestones: [https://github.com/Vexa-ai/vexa/milestones](https://github.com/Vexa-ai/vexa/milestones)

> For discussion/support, join our [Discord](https://discord.gg/Ga9duGkVz9).

## Architecture

**Core API services** (always running):


| Service                               | Purpose                                                                                                                        |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| [api-gateway](./services/api-gateway) | Reverse proxy — routes REST, WebSocket, VNC, CDP to backends                                                                   |
| [admin-api](./services/admin-api)     | User/org CRUD, scoped API tokens, team management                                                                              |
| [meeting-api](./services/meeting-api) | **Data layer** — bot lifecycle, meeting CRUD, recordings, transcription collector, interactive bot controls                    |
| [agent-api](./services/agent-api)     | **Intelligence layer** *(experimental)* — agent sessions, Claude CLI streaming, workspace sync                                 |
| [runtime-api](./services/runtime-api) | **Infrastructure layer** — container CRUD, spawn/stop/exec, port mapping, idle timeout. Docker, Kubernetes, or process backend |


**Meeting & AI services:**


| Service                                                   | Purpose                                                                       |
| --------------------------------------------------------- | ----------------------------------------------------------------------------- |
| [vexa-bot](./services/vexa-bot)                           | Joins meetings, captures per-speaker audio, transcribes, interactive controls |
| [transcription-service](./services/transcription-service) | GPU inference — OpenAI-compatible Whisper API                                 |
| [tts-service](./services/tts-service)                     | Text-to-speech for bot voice                                                  |
| [mcp](./services/mcp)                                     | 17-tool MCP server for AI agents (Claude, Cursor, etc.)                       |


**Frontends & clients:**


| Service                           | Purpose                                                                          |
| --------------------------------- | -------------------------------------------------------------------------------- |
| [dashboard](./services/dashboard) | Open-source Next.js web UI — meetings, transcripts, agent chat, browser sessions |


**Ephemeral containers** (spawned on demand, auto-reclaimed):


| Profile     | RAM    | Use case                                                       |
| ----------- | ------ | -------------------------------------------------------------- |
| **browser** | ~1.5GB | Meeting attendance, authenticated browser sessions (VNC + CDP) |
| **agent**   | ~200MB | Claude CLI, post-meeting processing, automation                |
| **worker**  | ~50MB  | Webhook delivery, file processing                              |


- Database models: `libs/admin-models/` (users, tokens), `services/meeting-api/` (meetings, transcriptions)



> If you're building with Vexa, we'd love your support! [Star our repo](https://github.com/Vexa-ai/vexa/stargazers) to help us reach 2000 stars.

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

**Agent runtime** *(experimental)*:

- **Ephemeral containers** — browser/agent/worker profiles, zero idle cost, ~5s spin-up
- **Remote browser** — VNC for human control + CDP for agent automation, persistent auth
- **MCP server** — 17 tools for Claude, Cursor, Windsurf — read + write + control meetings

**Platform:**

- **Multi-tenant** — users, orgs, scoped API tokens, container isolation
- **Webhooks** — push events for post-meeting automation pipelines
- **REST API** — complete API for bots, users, transcripts, recordings
- **Self-hostable** — full data sovereignty, Apache-2.0 licensed
- **Open-source frontends** — [Vexa Dashboard](./services/dashboard)

**Deployment & Management Guides:**

- [Vexa Lite Deployment Guide](https://docs.vexa.ai/vexa-lite-deployment) - Single container deployment
- [Docker Compose Deployment](https://docs.vexa.ai/deployment) - Full stack for development
- [Self-Hosted Management Guide](https://docs.vexa.ai/self-hosted-management) - Managing users and API tokens
- [Recording Storage](https://docs.vexa.ai/recording-storage) - S3, MinIO, and local storage configuration

## Modular — Pick What You Need

Vexa is a toolkit, not a monolith. Every feature works independently. Use one or all twelve — they compose when you need them to.


| You're building...                   | Features you need                                      | Skip the rest                     |
| ------------------------------------ | ------------------------------------------------------ | --------------------------------- |
| **Self-hosted Otter replacement**    | transcription + multi-platform + webhooks              | agent runtime, scheduler, MCP     |
| **Meeting data pipeline**            | transcription + webhooks + post-meeting                | speaking-bot, chat, agent runtime |
| **AI meeting assistant product**     | transcription + MCP + speaking-bot + chat              | remote-browser, scheduler         |
| **Personal AI assistant**            | agentic-runtime + workspaces + MCP                     | multi-platform, webhooks          |
| **Meeting bot API (like Recall.ai)** | multi-platform + transcription + token-scoping         | agent runtime, workspaces         |


You don't pay complexity tax for features you don't use. Each service is a separate container. Don't need agents? Don't run agent-api. Don't need TTS? Don't run tts-service. The architecture is modular by design — services communicate via REST and Redis, not tight coupling.

## Features — Honest Status

Each feature has its own README with business context, architecture, DoD table, and confidence score. **Confidence scores are evidence-based** — calculated from DoD pass/fail items and tests3 checks. We update these continuously.


| Feature                                                              | Confidence | Status                                                      |
| -------------------------------------------------------------------- | ---------- | ----------------------------------------------------------- |
| [meeting-urls](./features/meeting-urls/)                             | 100        | All 9 checks PASS. Teams e2e validated.                     |
| [browser-session](./features/browser-session/)                       | 95         | 18/18 DoD PASS. Google login persistence validated.         |
| [infrastructure](./features/infrastructure/)                         | 95         | All items pass including build.                             |
| [bot-lifecycle](./features/bot-lifecycle/)                           | 92         | 12/14 PASS. Unauthenticated join + escalation untested.     |
| [container-lifecycle](./features/container-lifecycle/)               | 100        | 15/15 PASS including K8s profiles.                          |
| [webhooks](./features/webhooks/)                                     | 100        | All 6 items PASS.                                           |
| [remote-browser](./features/remote-browser/)                         | 100        | All 6 items PASS.                                           |
| [dashboard](./features/dashboard/)                                   | 90         | 14/15 PASS. 1 false-failed meeting in production data.      |
| [meeting-chat](./features/meeting-chat/)                             | 0          | Not tested. No test target.                                 |
| [authenticated-meetings](./features/authenticated-meetings/)         | 75         | 8/10 PASS. Fallback FAIL, Teams auth not implemented.       |
| [speaking-bot](./features/speaking-bot/)                             | 70         | Single bot TTS reliable. Multi-bot drops under load.        |
| [auth-and-limits](./features/auth-and-limits/)                       | 70         | Ceiling items pass. Rate limiting + token CRUD untested.    |
| [realtime-transcription](./features/realtime-transcription/)         | 80         | GMeet + Teams work on all deployments. Zoom not implemented.|
| [post-meeting-transcription](./features/post-meeting-transcription/) | 40         | Realtime works. Recording on compose+K8s. Deferred untested.|


### Services


| Service                                                    | Confidence | Status                                                     |
| ---------------------------------------------------------- | ---------- | ---------------------------------------------------------- |
| [agent-api](./services/agent-api/)                         | 90         | 32 checks, CLI commands validated.                         |
| [vexa-bot](./services/vexa-bot/)                           | 80         | All items covered via feature tests. Zoom untested.        |
| [dashboard](./services/dashboard/)                         | 75         | 5/6 items via tests3 checks. npm test not run.             |
| [api-gateway](./services/api-gateway/)                     | 68         | Core routing, auth, WebSocket validated.                   |
| [admin-api](./services/admin-api/)                         | 62         | User/token CRUD, schema sync tested.                       |
| [transcription-service](./services/transcription-service/) | 55         | Health + transcription + auth pass. Backpressure untested. |
| [runtime-api](./services/runtime-api/)                     | 52         | Container CRUD, idle, callbacks tested.                    |
| [meeting-api](./services/meeting-api/)                     | 52         | Bot create, status, URL parsing. Tech debt remains.        |
| [tts-service](./services/tts-service/)                     | 30         | Indirect only — works via speaking-bot feature.            |
| [mcp](./services/mcp/)                                     | 0          | Untested.                                                  |
| [calendar-service](./services/calendar-service/)           | 0          | Experimental, not in default compose.                      |
| [telegram-bot](./services/telegram-bot/)                   | 0          | Experimental, not in default compose.                      |


### Deployments


| Mode                         | Confidence | Status                                                           |
| ---------------------------- | ---------- | ---------------------------------------------------------------- |
| [compose](./deploy/compose/) | 93         | 19/19 DoD PASS. Full stack validated.                            |
| [lite](./deploy/lite/)       | 85         | 12/12 DoD PASS. Recording disabled (no MinIO).                   |
| [helm](./deploy/helm/)       | 90         | 9/10 DoD PASS. Built images + global.imageTag validated on LKE.  |


## Related Projects

Vexa is part of an ecosystem of open-source tools:

### [Vexa Dashboard](./services/dashboard)

100% open-source web interface for Vexa, included in this monorepo at `services/dashboard/`. Join meetings, view transcripts, chat with agents, manage browser sessions, and more. Self-host everything with no cloud dependencies.

## Contributing

We use **GitHub Issues** as our main feedback channel. New issues are triaged within **72 hours** (you'll get a label + short response). Not every feature will be implemented, but every issue will be acknowledged. Look for `**good-first-issue`** if you want to contribute.

Contributors are welcome! Join our community and help shape Vexa's future. Here's how to get involved:

1. **Understand Our Direction**:
2. **Engage on Discord** ([Discord Community](https://discord.gg/Ga9duGkVz9)):
  - **Introduce Yourself**: Start by saying hello in the introductions channel.
  - **Stay Informed**: Check the Discord channel for known issues, feature requests, and ongoing discussions. Issues actively being discussed often have dedicated channels.
  - **Discuss Ideas**: Share your feature requests, report bugs, and participate in conversations about a specific issue you're interested in delivering.
  - **Get Assigned**: If you feel ready to contribute, discuss the issue you'd like to work on and ask to get assigned on Discord.
3. **Development Process**:
  - Browse available **tasks** (often linked from Discord discussions or the roadmap).
  - Request task assignment through Discord if not already assigned.
  - Submit **pull requests** for review.

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

[Meet Founder](https://www.linkedin.com/in/dmitry-grankin/)

[Join Discord](https://discord.gg/Ga9duGkVz9)

The Vexa name and logo are trademarks of **Vexa.ai Inc**.