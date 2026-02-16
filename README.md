<p align="center" style="margin-bottom: 0.75em;">
  <img src="assets/logodark.svg" alt="Vexa Logo" width="56"/>
</p>

<h1 align="center" style="margin-top: 0.25em; margin-bottom: 0.5em; font-size: 2.5em; font-weight: 700; letter-spacing: -0.02em;">Vexa</h1>

<p align="center" style="font-size: 1.75em; margin-top: 0.5em; margin-bottom: 0.75em; font-weight: 700; line-height: 1.3; letter-spacing: -0.01em;">
  <strong>Self-hosted meeting intelligence platform</strong>
</p>

<p align="center" style="font-size: 1em; color: #a0a0a0; margin-top: 0.5em; margin-bottom: 1.5em; letter-spacing: 0.01em;">
  bots • real-time transcription • storage • API • user interface
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
  <a href="#whats-new">What’s new</a> •
  <a href="#quickstart">Quickstart</a> •
  <a href="#2-get-transcripts">API</a> •
  <a href="https://docs.vexa.ai">Docs</a> •
  <a href="#roadmap">Roadmap</a> •
  <a href="https://discord.gg/Ga9duGkVz9">Discord</a>
</p>

---

## What is Vexa?

**Vexa** is an open-source, self-hostable API for real-time meeting transcription. It automatically joins Google Meet, Microsoft Teams, and Zoom meetings, captures audio, and provides real-time transcriptions via REST API and WebSocket.

### At a glance

| Capability | What it means |
|---|---|
| **Meeting bots** | Automatically joins Google Meet, Microsoft Teams, and Zoom meetings |
| **Real-time transcription** | Sub-second transcript delivery during the call |
| **Interactive bots** | Make bots speak, send/read chat, share screen content, and set avatar in live meetings |
| **Multilingual** | 100+ languages via Whisper (transcription + translation) |
| **API-first** | REST API + WebSocket streaming for integrations |
| **MCP-ready** | Connect AI agents (Claude/Cursor/etc.) through the MCP server |
| **Storage** | Persist transcripts + meeting metadata in your database |
| **Multi-user** | Team-ready: users, API keys/tokens, admin operations |
| **Self-hostable** | Run on your infra for complete data sovereignty |
| **User interfaces** | Open-source frontends (currently: **[Vexa Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)**) |

### Who it's for

| You are... | You want... |
|---|---|
| **Enterprises** | Self-hosted transcription with strict privacy requirements |
| **Small & medium teams** | Simple deployment (Vexa Lite) with an open-source UI |
| **Developers** | Build meeting products (assistants, automations, analytics) on top of the API |
| **Automation builders** | Integrate with tools like n8n via webhooks / APIs |

---

## Build on Top. In Hours, Not Months

**Build powerful meeting assistants (like Otter.ai, Fireflies.ai, Fathom) for your startup, internal use, or custom integrations.**

The Vexa API provides powerful abstractions and a clear separation of concerns, enabling you to build sophisticated applications on top with a safe and enjoyable coding experience.

## 🛡️ Built for Data Sovereignty

Vexa is open-source and self-hostable — ideal for regulated industries and teams that cannot compromise on privacy. 

Modular architecture scales from edge devices to millions of users. You choose what to self-host and what to use as a service.

**You control everything:**

**1. Full self-hosting**  
Run Vexa, database, and transcription service entirely on your infrastructure  
*<small style="color: #999;">For regulated industries like fintech, medical, etc.</small>*

<hr style="margin: 1.25em 0; border: none; border-top: 1px solid #333;">

**2. GPU-free self-hosting**  
Self-host Vexa, but plug into external transcription service  
*<small style="color: #999;">Perfect privacy with minimal DevOps</small>*

<hr style="margin: 1.25em 0; border: none; border-top: 1px solid #333;">

**3. Fully hosted service**  
At [vexa.ai](https://vexa.ai) — just grab API key  
*<small style="color: #999;">Ready to integrate</small>*


<a id="whats-new"></a>

## 🎉 What's new in v0.9 (pre-release)

- **Zoom:** initial Zoom Meeting SDK support (requires Zoom app setup/approval; see docs)
- **Recordings:** persist recording artifacts to S3-compatible storage (or local)
- **Post-meeting playback:** stream recordings via `/recordings/.../raw` with `Range` seeking (`206`) + `Content-Disposition: inline`
- **Delete semantics:** deleting a meeting also purges recording objects/artifacts (best-effort) before anonymizing the meeting
- **Interactive Bots API:** live controls for speak/chat/screen/avatar during active meetings
- **MCP integration docs:** end-to-end guide for connecting AI agents to Vexa tools

---

> See full release notes: https://github.com/Vexa-ai/vexa/releases

---

## Quickstart

### Option 1: Hosted (Fastest)

Just grab your API key at [https://vexa.ai/dashboard/api-keys](https://vexa.ai/dashboard/api-keys) and start using the service immediately.

### Option 2: Vexa Lite - For Users (Recommended for Production)

**Self-hosted, multiuser service for teams. Run as a single Docker container for easy deployment.**

Vexa Lite is a single-container deployment perfect for teams who want:
- **Self-hosted multiuser service** - Multiple users, API tokens, and team management
- **Quick deployment** on any platform - Single container, easy to deploy
- **No GPU required** - Transcription runs externally
- **Choose your frontend** - Pick from open-source user interfaces like [Vexa Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)
- **Production-ready** - Stateless, scalable, serverless-friendly

**Quick start:**
```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://user:pass@host/vexa" \
  -e ADMIN_API_TOKEN="your-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.service" \
  -e TRANSCRIBER_API_KEY="transcriber-token" \
  vexaai/vexa-lite:latest
```

**Deployment options:**
- 🚀 **One-click platform deployments**: [vexa-lite-deploy repository](https://github.com/Vexa-ai/vexa-lite-deploy)
  - ✅ **Fly.io** - Implemented
  - 🚧 **Railway, Render, etc.** - To be added (contribute by adding your platform of choice!)
- 📖 **Complete setup guide**: [Vexa Lite Deployment Guide](https://docs.vexa.ai/vexa-lite-deployment) - Environment variables, storage, TTS, and all configuration options
- 🎨 **Frontend options**: Choose from open-source user interfaces like [Vexa Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)

### Option 3: Docker Compose - For Development

**Full stack deployment with all services. Perfect for development and testing.**

All services are saved in `docker-compose.yml` and wrapped in a Makefile for convenience:

```bash
git clone https://github.com/Vexa-ai/vexa.git
cd vexa
make all                         # Default: remote transcription (GPU-free)
```

**What `make all` does:**
- Builds all Docker images
- Spins up all containers (API, bots, transcription services, database)
- Runs database migrations
- Starts a simple test to verify everything works

* Full guide: [Deployment Guide](https://docs.vexa.ai/deployment)

### Recording storage (local and cloud)

Recording is implemented and supports local filesystem, MinIO, and cloud S3-compatible backends.

See [Recording Storage](https://docs.vexa.ai/recording-storage) for:

- Storage backends and environment variables (`STORAGE_BACKEND`)
- Docker Compose / Lite / Kubernetes deployment notes
- Browser playback details (`/recordings/{recording_id}/media/{media_file_id}/raw`, `Range`/`206`, `Content-Disposition: inline`)

### Option 4: Hashicorp Nomad, Kubernetes, OpenShift

For enterprise orchestration platforms, contact [vexa.ai](https://vexa.ai)

## 1. Send bot to meeting:

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

## 2. Get transcripts:

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

* **Enterprises (self-host):** Data sovereignty and control on your infra
* **Teams using hosted API:** Fastest path from meeting to transcript
* **n8n/indie builders:** Low-code automations powered by real-time transcripts
  - Tutorial: https://vexa.ai/blog/google-meet-transcription-n8n-workflow

---

## Roadmap

For the up-to-date roadmap and priorities, see GitHub Issues and Milestones. Issues are grouped by milestones to show what's coming next, in what order, and what's currently highest priority.

- Issues: https://github.com/Vexa-ai/vexa/issues
- Milestones: https://github.com/Vexa-ai/vexa/milestones

> For discussion/support, join our [Discord](https://discord.gg/Ga9duGkVz9).

## Architecture

- [api-gateway](./services/api-gateway): Routes API requests to appropriate services
- [mcp](./services/mcp): Provides MCP-capable agents with Vexa as a toolkit
- [bot-manager](./services/bot-manager): Handles bot lifecycle management
- [vexa-bot](./services/vexa-bot): The bot that joins meetings and captures audio
- [WhisperLive](./services/WhisperLive): Real-time audio transcription service (uses transcription-service as backend in remote mode)
- [transcription-service](./services/transcription-service): Basic transcription service (WhisperLive uses it as a real-time wrapper)
- [transcription-collector](./services/transcription-collector): Processes and stores transcription segments
- [Database models](./libs/shared-models/shared_models/models.py): Data structures for storing meeting information

> 💫 If you're building with Vexa, we'd love your support! [Star our repo](https://github.com/Vexa-ai/vexa/stargazers) to help us reach 2000 stars.

### Features:

- **Real-time multilingual transcription** supporting **100 languages** with **Whisper**
- **Real-time translation** across all 100 supported languages
- **Google Meet integration** - Automatically join and transcribe Google Meet calls
- **Microsoft Teams integration** - Automatically join and transcribe Teams meetings
- **Zoom integration** - Automatically join and transcribe Zoom meetings
- **REST API** - Complete API for managing bots, users, and transcripts
- **Interactive meeting controls** - Bot speak/chat/screen/avatar endpoints for active meetings
- **WebSocket streaming** - Sub-second transcript delivery via WebSocket
- **MCP server** - Expose Vexa APIs as agent tools for MCP-compatible clients
- **Multiuser support** - User management, API tokens, and team features
- **Self-hostable** - Full control over your data and infrastructure
- **Open-source frontends** - Choose from user interfaces like [Vexa Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)

**Deployment & Management Guides:**
- [Vexa Lite Deployment Guide](https://docs.vexa.ai/vexa-lite-deployment) - Single container deployment
- [Docker Compose Deployment](https://docs.vexa.ai/deployment) - Full stack for development
- [Self-Hosted Management Guide](https://docs.vexa.ai/self-hosted-management) - Managing users and API tokens
- [Recording Storage](https://docs.vexa.ai/recording-storage) - S3, MinIO, and local storage configuration

## Related Projects

Vexa is part of an ecosystem of open-source tools:


### 🎨 [Vexa Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)
100% open-source web interface for Vexa. Join meetings, view transcripts, manage users, and more. Self-host everything with no cloud dependencies.

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
- **[Vexa-Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)** - Web UI for managing Vexa instances (first in a planned series of UI applications)

[![Meet Founder](https://img.shields.io/badge/LinkedIn-Dmitry_Grankin-0A66C2?style=flat-square&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/dmitry-grankin/)

[![Join Discord](https://img.shields.io/badge/Discord-Community-5865F2?style=flat-square&logo=discord&logoColor=white)](https://discord.gg/Ga9duGkVz9)

The Vexa name and logo are trademarks of **Vexa.ai Inc**.
