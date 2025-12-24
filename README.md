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
  <img height="24" src="assets/icons8-zoom.svg" alt="Zoom" style="vertical-align: middle; margin-right: 10px;"/> <strong style="font-size: 1em; font-weight: 600;">Zoom</strong> <sub style="font-size: 0.7em; color: #999; font-weight: normal; margin-left: 4px;">(soon)</sub>
</p>

<p align="center" style="margin: 1.75em 0 1.25em 0;">
  <a href="https://github.com/Vexa-ai/vexa/stargazers"><img src="https://img.shields.io/github/stars/Vexa-ai/vexa?style=flat-square&color=yellow" alt="Stars"/></a>
  &nbsp;&nbsp;&nbsp;
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square" alt="License"/></a>
  &nbsp;&nbsp;&nbsp;
  <a href="https://discord.gg/Ga9duGkVz9"><img src="https://img.shields.io/badge/Discord-join-5865F2?style=flat-square&logo=discord&logoColor=white" alt="Discord"/></a>
</p>

<p align="center">
  <a href="#-whats-new-in-v07-18-dec-2025">What’s new</a> •
  <a href="#quickstart">Quickstart</a> •
  <a href="#2-get-transcripts">API</a> •
  <a href="#roadmap">Roadmap</a> •
  <a href="https://discord.gg/Ga9duGkVz9">Discord</a>
</p>

---

## What is Vexa?

**Vexa** is an open-source, self-hostable API for real-time meeting transcription. It automatically joins Google Meet and Microsoft Teams meetings, captures audio, and provides real-time transcriptions via REST API and WebSocket.

### At a glance

| Capability | What it means |
|---|---|
| **Meeting bots** | Automatically joins Google Meet + Microsoft Teams meetings |
| **Real-time transcription** | Sub-second transcript delivery during the call |
| **Multilingual** | 100+ languages via Whisper (transcription + translation) |
| **API-first** | REST API + WebSocket streaming for integrations |
| **Storage** | Persist transcripts + meeting metadata in your database |
| **Multi-user** | Team-ready: users, API keys/tokens, admin operations |
| **Self-hostable** | Run on your infra for complete data sovereignty |
| **User interfaces** | Open-source frontends (currently: **[Vexa Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)**) |

### How it works

<p align="center">
  <img src="assets/product-diagram.png" alt="How Vexa Works" width="100%"/>
</p>

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


## 🎉 What's new in v0.7 (pre-release)

- **Vexa Lite:** run Vexa as a **single Docker container** (`vexaai/vexa-lite:latest`)
- **Optional external transcription:** point Lite to an external service to avoid GPU requirements
- **Stateless by design:** all state lives in your DB → easy redeploy/scale
- **Serverless-friendly:** minimal footprint, fewer moving parts, faster deployments

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
- 📖 **Complete setup guide**: [Vexa Lite Deployment Guide](docs/vexa-lite-deployment.md) - All 4 configurations (local/remote database, local/remote transcription)
- 🎨 **Frontend options**: Choose from open-source user interfaces like [Vexa Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)

### Option 3: Docker Compose - For Development

**Full stack deployment with all services. Perfect for development and testing.**

All services are saved in `docker-compose.yml` and wrapped in a Makefile for convenience:

```bash
git clone https://github.com/Vexa-ai/vexa.git
cd vexa
make all            # CPU by default (Whisper tiny) — good for development
# For GPU:
# make all TARGET=gpu    # (Whisper medium) — recommended for production quality
```

**What `make all` does:**
- Builds all Docker images
- Spins up all containers (API, bots, transcription services, database)
- Runs database migrations
- Starts a simple test to verify everything works

* Full guide: [docs/deployment.md](docs/deployment.md)

### Option 4: Hashicorp Nomad, Kubernetes, OpenShift

For enterprise orchestration platforms, contact [vexa.ai](https://vexa.ai)

## 1. Send bot to meeting:

`API_HOST` for hosted version is `https://api.cloud.vexa.ai`
`API_HOST` for self-hosted lite container is `http://localhost:8056`
`API_HOST` for self-hosted full stack (default) is `http://localhost:18056`

### Request a bot for Microsoft Teams

```bash
curl -X POST https://<API_HOST>/bots \
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
curl -X POST https://<API_HOST>/bots \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij"
  }'
```

## 2. Get transcripts:

### Get transcripts over REST

```bash
curl -H "X-API-Key: <API_KEY>" \
  "https://<API_HOST>/transcripts/<platform>/<native_meeting_id>"
```

For real-time streaming (sub‑second), see the [WebSocket guide](docs/websocket.md).
For full REST details, see the [User API Guide](docs/user_api_guide.md).

Note: Meeting IDs are user-provided (Google Meet code like `xxx-xxxx-xxx` or Teams numeric ID and passcode). Vexa does not generate meeting IDs.

---

## Who Vexa is for

* **Enterprises (self-host):** Data sovereignty and control on your infra
* **Teams using hosted API:** Fastest path from meeting to transcript
* **n8n/indie builders:** Low-code automations powered by real-time transcripts
  - Tutorial: https://vexa.ai/blog/google-meet-transcription-n8n-workflow

---

## Roadmap

* Zoom support (coming soon)

> For issues and progress, join our [Discord](https://discord.gg/Ga9duGkVz9).

## Architecture

<p align="center">
  <img src="assets/simplified_flow.png" alt="Vexa Architecture Flow" width="100%"/>
</p>

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
- **REST API** - Complete API for managing bots, users, and transcripts
- **WebSocket streaming** - Sub-second transcript delivery via WebSocket
- **Multiuser support** - User management, API tokens, and team features
- **Self-hostable** - Full control over your data and infrastructure
- **Open-source frontends** - Choose from user interfaces like [Vexa Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)

**Deployment & Management Guides:**
- [Local Deployment and Testing Guide](docs/deployment.md)
- [Self-Hosted Management Guide](docs/self-hosted-management.md) - Managing users and API tokens
- [Vexa Lite Deployment Guide](docs/vexa-lite-deployment.md) - Single container deployment

## Related Projects

Vexa is part of an ecosystem of open-source tools:


### 🎨 [Vexa Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)
100% open-source web interface for Vexa. Join meetings, view transcripts, manage users, and more. Self-host everything with no cloud dependencies.

## Contributing

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

## Contributing & License

We ❤️ contributions. Join our Discord and open issues/PRs.
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
