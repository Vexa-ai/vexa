<p align="center" style="margin-bottom: 0.75em;">
  <img src="assets/logodark.svg" alt="Vexa Logo" width="56"/>
</p>

<h1 align="center" style="margin-top: 0.25em; margin-bottom: 0.5em; font-size: 2.5em; font-weight: 700; letter-spacing: -0.02em;">Vexa</h1>

<p align="center" style="font-size: 1.25em; margin-top: 0.75em; margin-bottom: 0.5em; font-weight: 600; line-height: 1.4;">
  <strong>Self-hosted meeting intelligence platform</strong>
</p>

<p align="center" style="font-size: 1em; color: #a0a0a0; margin-top: 0.5em; margin-bottom: 1.5em; letter-spacing: 0.01em;">
  bots • real-time transcription • storage • API
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


## 🎉 What's new in v0.7 (18 Dec 2025)

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

### Option 2: Self-host with Lite Container (Single Container, No GPU)

```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://user:pass@host:5432/vexa" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.example.com/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="token" \
  vexaai/vexa-lite:latest
```

**Deployment options:** Mix and match based on your needs:

**Transcription service:**

1. Get [API key for hosted transcription service](https://vexa.ai) (faster, GPU-free)
   or
2. [Self-host transcription service](services/transcription-service/README.md)

**Database:**

1. [Connect remote database](docker/lite/DEPLOYMENT_GUIDE.md#option-a-use-hosted-database) — Good practice for production
   or
2. [Setup local database](docker/lite/DEPLOYMENT_GUIDE.md#option-b-run-local-postgresql) (Faster start)

📖 **For detailed setup instructions and examples for each configuration**, see [docker/lite/DEPLOYMENT_GUIDE.md](docker/lite/DEPLOYMENT_GUIDE.md)

### Option 3: Self-host with Docker Compose

Good for development:

```bash
git clone https://github.com/Vexa-ai/vexa.git
cd vexa
make all            # CPU by default (Whisper tiny) — good for development
# For GPU:
# make all TARGET=gpu    # (Whisper medium) — recommended for production quality
```

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
    "native_meeting_id": "<MEET_CODE_XXX-XXXX-XXX>"
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

## Build on Top. In Hours, Not Months

**Build powerful meeting assistants (like Otter.ai, Fireflies.ai, Fathom) for your startup, internal use, or custom integrations.**

The Vexa API provides powerful abstractions and a clear separation of concerns, enabling you to build sophisticated applications on top with a safe and enjoyable coding experience.

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

## Current Status

- **Public API**: Fully available with self-service API keys at [www.vexa.ai](https://www.vexa.ai/?utm_source=github&utm_medium=readme&utm_campaign=vexa_repo)
- **Google Meet Bot:** Fully operational bot for joining Google Meet calls
- **Teams Bot:** Fully operational bot for joining Teams calls
- **Real-time Transcription:** Low-latency, multilingual transcription service is live
- **Real-time Translation:** Instant translation between 100 supported languages
- **WebSocket Streaming:** Sub-second transcript delivery via WebSocket API

## Coming Next

- **Zoom Bot:** Integration for automated meeting attendance (coming soon)
- **Direct Streaming:** Ability to stream audio directly from web/mobile apps

## Self-Deployment

For **security-minded companies**, Vexa offers complete **self-deployment** options.

To run Vexa locally on your own infrastructure, the primary command you'll use after cloning the repository is `make all`. This command sets up the environment (CPU by default, or GPU if specified), builds all necessary Docker images, and starts the services.

**Deployment & Management Guides:**
- [Local Deployment and Testing Guide](docs/deployment.md)
- [Self-Hosted Management Guide](docs/self-hosted-management.md) - Managing users and API tokens

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

[![Meet Founder](https://img.shields.io/badge/LinkedIn-Dmitry_Grankin-0A66C2?style=flat-square&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/dmitry-grankin/)

[![Join Discord](https://img.shields.io/badge/Discord-Community-5865F2?style=flat-square&logo=discord&logoColor=white)](https://discord.gg/Ga9duGkVz9)

The Vexa name and logo are trademarks of **Vexa.ai Inc**.
