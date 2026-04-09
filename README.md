# **Vexa**

**Open-source meeting bot API & transcription API**

meeting bots • real-time transcription • interactive bots • MCP server • self-hosted

**Google Meet**     •     **Microsoft Teams**     •     **Zoom** (experimental)

       

[What's new](#whats-new) • [Quickstart](#quickstart) • [API](#meeting-api--send-bots-get-transcripts) • [Docs](https://docs.vexa.ai) • [Roadmap](#roadmap) • [Discord](https://discord.gg/Ga9duGkVz9)

---

**Vexa** is an open-source, self-hostable meeting bot API and meeting transcription API for Google Meet, Microsoft Teams, and Zoom. Alternative to Recall.ai, Otter.ai, and Fireflies.ai — self-host so meeting data never leaves your infrastructure, or use [vexa.ai](https://vexa.ai) hosted.

---

**Data sovereignty** — self-host so meeting data never leaves your infrastructure

**Cost** — replace $20/seat SaaS with your own infrastructure

**Embed in your product** — multi-tenant meeting bot API with scoped tokens

**AI agents** — MCP server with 17 tools

---

### Capabilities


| | |
| --- | --- |
| **Meeting bot API** | Send a bot to any meeting: auto-join, record, speak, chat, share screen. Open-source alternative to [Recall.ai](https://recall.ai). |
| **Meeting transcription API** | Real-time transcripts via REST API and WebSocket. Self-hosted alternative to Otter.ai and Fireflies.ai. |
| **Real-time transcription**   | Sub-second per-speaker transcripts during the call. 100+ languages via Whisper. WebSocket streaming.                                |
| **Interactive bots**          | Make bots speak, send/read chat, share screen content, and set avatar in live meetings.                                             |
| **Browser bots**              | CDP + Playwright browser automation with persistent authenticated sessions via S3.                                                  |
| **MCP server**                | 17 meeting tools for Claude, Cursor, Windsurf. AI agents join calls, read transcripts, speak in meetings.                           |
| **Multi-tenant**              | Users, scoped API tokens, isolated containers. Deploy once, serve your team.                                                        |
| **Self-hostable**             | Run on your infra. Meeting data never leaves your infrastructure.                                                                   |


Every feature is a separate service. Pick what you need, skip what you don't. Self-host everything or use [vexa.ai](https://vexa.ai) hosted.

---

## Why Self-Host Meeting Transcription?

**For regulated industries** — banks, financial services, healthcare — meeting data can't leave your infrastructure. Self-hosting Vexa means zero external data transmission and full audit trail on your own infrastructure.

**For cost-conscious teams** — replace per-seat SaaS pricing. A team paying $17/seat/mo for meeting transcription can self-host Vexa and drop that to infrastructure cost.

**For developers** — embed a meeting bot API in your product. Multi-tenant, scoped API tokens, no per-user infrastructure.

Build meeting assistants like Otter.ai, Fireflies.ai, or Fathom — or build a meeting bot API like [Recall.ai](https://recall.ai) — self-hosted on your infrastructure.

- **Vexa (self-host)** — your infra cost. Data never leaves your infrastructure. Meeting bot API, real-time transcription, interactive bots, MCP server. Open source, Apache 2.0. Google Meet, Teams, Zoom*.
- **Recall.ai** — $0.50/hr. No self-hosting. Meeting bot API, real-time transcription. No MCP, limited interactive bots. Closed source. Meet, Teams, Zoom, Webex.
- **Otter.ai** — $17-20/seat/mo. No self-hosting. No API. Limited real-time transcription. Closed source. Meet, Teams, Zoom.

 Zoom support is experimental

Or use [vexa.ai](https://vexa.ai) hosted — get an API key and start sending bots immediately, no infrastructure needed.

## Built for Data Sovereignty

Meeting data never leaves your infrastructure. Self-host for complete control. Modular architecture scales from edge devices to millions of users.

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
*Meeting data never leaves your infrastructure* — see [deploy/](./deploy/) for setup guides.

## What's new

**v0.10 — full architecture refactor**

- **Services refactored** — runtime-api as infrastructure layer (container orchestration), meeting-api as data layer, agent-api as high-level intelligence layer. Clean separation of concerns.
- **Real-time pipeline moved into bots** — transcription pipeline now runs inside bot containers, eliminating external dependencies
- **Vexa Lite** — single-container deployment for self-hosting. One Docker image, no GPU required.
- **Helm/K8s** — production Kubernetes deployment with built images and global.imageTag support
- **Agent API** *(experimental)* — ephemeral containers for AI agents. See [services/agent-api/](./services/agent-api/).

**v0.9**

- **Zoom** *(experimental)* — initial Zoom Meeting SDK support
- **Interactive Bots API** — speak, chat, screen share, avatar controls during live meetings
- **MCP server** — 17 tools for AI agents
- **Recordings** — S3-compatible storage

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

## Meeting API — Send Bots, Get Transcripts

Send a bot, get real-time transcripts with per-speaker audio and interactive controls (speak, chat, share screen).

```bash
# Send a bot to Google Meet
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}'

# Get transcripts
curl -H "X-API-Key: <API_KEY>" \
  "$API_BASE/transcripts/google_meet/abc-defg-hij"
```

Works with Google Meet, Microsoft Teams, and Zoom. Set `API_BASE` to `https://api.cloud.vexa.ai` (hosted) or `http://localhost:8056` (self-hosted).

For real-time WebSocket streaming, see the [WebSocket guide](https://docs.vexa.ai/websocket). For full REST details, see the [User API Guide](https://docs.vexa.ai/user_api_guide).

---

## Browser Bots — Persistent Browser Containers for Agents

Remote browser containers with CDP + Playwright access and persistent session storage via S3. Agents get a real browser that stays logged in across restarts — Google, Microsoft, or any web session.

- **CDP + Playwright** — full browser automation via Chrome DevTools Protocol
- **Persistent sessions** — authenticated browser state saved to S3, restored on next spin-up
- **VNC access** — humans can observe and control the browser in real time alongside agents
- **On-demand containers** — spin up in seconds, auto-reclaim when idle

See [features/browser-session/](./features/browser-session/) and [features/remote-browser/](./features/remote-browser/) for details.

---

## MCP Server — Meeting Tools for AI Agents

17 tools that let AI agents join meetings, read transcripts, speak, chat, and share screen. Works with Claude, Cursor, Windsurf, and any MCP-compatible client.

Your AI agent can join a meeting, listen to the conversation, and participate — all through MCP tool calls. See [services/mcp/](./services/mcp/) for setup and tool reference.

---

## Modular — Pick What You Need

Vexa is a toolkit, not a monolith. Every feature works independently. Use one or all — they compose when you need them to.


| You're building...                   | Features you need                              | Skip the rest                     |
| ------------------------------------ | ---------------------------------------------- | --------------------------------- |
| **Self-hosted Otter replacement**    | transcription + multi-platform + webhooks      | agent runtime, scheduler, MCP     |
| **Meeting data pipeline**            | transcription + webhooks + post-meeting        | speaking-bot, chat, agent runtime |
| **AI meeting assistant product**     | transcription + MCP + speaking-bot + chat      | remote-browser, scheduler         |
| **Meeting bot API (like Recall.ai)** | multi-platform + transcription + token-scoping | agent runtime, workspaces         |


You don't pay complexity tax for features you don't use. Each service is a separate container. Don't need agents? Don't run agent-api. Don't need TTS? Don't run tts-service. Services communicate via REST and Redis, not tight coupling.

---

## Roadmap

For the up-to-date roadmap and priorities, see GitHub Issues and Milestones. Issues are grouped by milestones to show what's coming next, in what order, and what's currently highest priority.

- Issues: [https://github.com/Vexa-ai/vexa/issues](https://github.com/Vexa-ai/vexa/issues)
- Milestones: [https://github.com/Vexa-ai/vexa/milestones](https://github.com/Vexa-ai/vexa/milestones)

> For discussion/support, join our [Discord](https://discord.gg/Ga9duGkVz9).

## Architecture & Feature Status

Each service and feature has its own README with architecture, DoD table, and evidence-based confidence scores.

- **Services:** [api-gateway](./services/api-gateway) • [meeting-api](./services/meeting-api) • [admin-api](./services/admin-api) • [runtime-api](./services/runtime-api) • [vexa-bot](./services/vexa-bot) • [transcription-service](./services/transcription-service) • [tts-service](./services/tts-service) • [mcp](./services/mcp) • [dashboard](./services/dashboard) • [agent-api](./services/agent-api) *(experimental)*
- **Features:** [realtime-transcription](./features/realtime-transcription) • [bot-lifecycle](./features/bot-lifecycle) • [browser-session](./features/browser-session) • [remote-browser](./features/remote-browser) • [speaking-bot](./features/speaking-bot) • [meeting-chat](./features/meeting-chat) • [webhooks](./features/webhooks) • [authenticated-meetings](./features/authenticated-meetings)
- **Deploy:** [Docker Compose](./deploy/compose) • [Vexa Lite](./deploy/lite) • [Helm/K8s](./deploy/helm)
- **Guides:** [Vexa Lite Deployment](https://docs.vexa.ai/vexa-lite-deployment) • [Docker Compose Deployment](https://docs.vexa.ai/deployment) • [Self-Hosted Management](https://docs.vexa.ai/self-hosted-management) • [Recording Storage](https://docs.vexa.ai/recording-storage)

---

## Contributing

We use **GitHub Issues** as our main feedback channel — triaged within **72 hours**. Look for `good-first-issue` to get started. Join [Discord](https://discord.gg/Ga9duGkVz9) to discuss ideas and get assigned.

Licensed under **Apache-2.0** — see [LICENSE](LICENSE).

## Links

[Website](https://vexa.ai) • [Docs](https://docs.vexa.ai) • [Discord](https://discord.gg/Ga9duGkVz9) • [LinkedIn](https://www.linkedin.com/company/vexa-ai/) • [X (@grankin_d)](https://x.com/grankin_d) • [Meet Founder](https://www.linkedin.com/in/dmitry-grankin/)

**Related:** [vexa-lite-deploy](https://github.com/Vexa-ai/vexa-lite-deploy) • [Vexa Dashboard](./services/dashboard)