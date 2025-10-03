<p align="left">
  <img src="assets/logodark.svg" alt="Vexa Logo" width="40"/>
</p>

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-join-5865F2.svg)](https://discord.gg/Ga9duGkVz9)
[![Open Source](https://img.shields.io/badge/Open%20Source-Yes-black.svg)](https://github.com/Vexa-ai/vexa)

# Vexa — Real-Time Meeting Transcription API (Meet & Teams) + WebSocket

**Vexa** drops a bot into your online meeting and streams transcripts to your apps in real time.  
- **Platforms:** Google Meet **and Microsoft Teams**  
- **Transport:** REST **or WebSocket (sub-second)**  
- **Run it your way:** Open source & self-hostable, or use the hosted API.

👉 **Website / Quickstart:** https://vexa.ai/get-started  
👉 **Self-host guide:** [DEPLOYMENT.md](DEPLOYMENT.md)

---

## What's new in v0.6

- **Microsoft Teams support** (alongside Google Meet)
- **WebSocket streaming** for efficient sub-second delivery
- Reliability & joining improvements from real-world usage

> See full release notes: https://github.com/Vexa-ai/vexa/releases

---

## Quickstart (Hosted)

> Get an API key in minutes at https://vexa.ai/get-started

### 1) Create a bot for **Microsoft Teams**
```bash
# POST /bots
curl -X POST https://<API_HOST>/bots \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "microsoft_teams",
    "meeting_url": "<TEAMS_MEETING_URL>",
    "language": "en"
  }'
```

### 2) Or create a bot for **Google Meet**

```bash
curl -X POST https://<API_HOST>/bots \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "google_meet",
    "meeting_url": "<MEET_URL>",
    "language": "en"
  }'
```

### 3) Stream transcripts over **WebSocket** (sub-second)

```js
// Minimal example; replace placeholders with real values
const url = "wss://<API_HOST>/ws/transcripts?bot_id=<BOT_ID>&token=<API_TOKEN>";
const ws = new WebSocket(url);

ws.onopen = () => console.log("WS connected");
ws.onmessage = (evt) => {
  const msg = JSON.parse(evt.data); // {type: "partial"|"final", ts, text, speaker? ...}
  console.log(msg.type, msg.text);
};
ws.onclose = () => console.log("WS closed");
ws.onerror = (e) => console.error("WS error", e);
```

### 4) (Optional) Poll over REST

```bash
curl -H "Authorization: Bearer <API_TOKEN>" \
  "https://<API_HOST>/transcripts/<platform>/<native_meeting_id>"
```

---

## Quickstart (Self-Host)

Open-source, privacy-first self-hosting with Docker Compose / Nomad.

```bash
git clone https://github.com/Vexa-ai/vexa.git
cd vexa
cp env-example.cpu .env  # or env-example.gpu
make all                 # builds & starts services locally
```

* Full guide: [DEPLOYMENT.md](DEPLOYMENT.md)
* Then use the same API calls as the hosted quickstart (pointing to your host).

---

## Who this is for

* **Enterprises (Self-host):** Data sovereignty & control; deploy on your infra.
* **SMB / Teams using hosted API:** Fastest path from meeting to transcript.
* **n8n & indie builders:** Zero/low-code automations powered by real-time transcripts.

  * n8n tutorial: https://vexa.ai/blog/google-meet-transcription-n8n-workflow

---

## Roadmap (short)

* Zoom support (public preview next)
* Docs polish for WS message schema, retry/back-pressure examples
* Latency methodology & benchmarks blog

> For issues and progress, see https://github.com/Vexa-ai/vexa/issues

## Build on Top. In Hours, Not Months

**Build powerful meeting assistants (like Otter.ai, Fireflies.ai, Fathom) for your startup, internal use, or custom integrations.**

The Vexa API provides powerful abstractions and a clear separation of concerns, enabling you to build sophisticated applications on top with a safe and enjoyable coding experience.

For instance, the **Vexa Example Client** (see [Projects Built with Vexa](BUILT-WITH-VEXA.md)) was built in just 3 hours of live coding with Cursor, showcasing the rapid development possible with Vexa.

Furthermore, with our **n8n integration** (see [Projects Built with Vexa](BUILT-WITH-VEXA.md) for examples), you can create incredibly complex workflows with no code, leveraging real-time transcription from Google Meet (with support for other platforms coming soon).

<p align="center">
  <img src="assets/simplified_flow.png" alt="Vexa Architecture Flow" width="100%"/>
</p>

- [api-gateway](./services/api-gateway): Routes API requests to appropriate services
- [mcp](./services/mcp): Provides MCP-capable agents with Vexa as a toolkit
- [bot-manager](./services/bot-manager): Handles bot lifecycle management
- [vexa-bot](./services/vexa-bot): The bot that joins meetings and captures audio
- [WhisperLive](./services/WhisperLive): Real-time audio transcription service
- [transcription-collector](./services/transcription-collector): Processes and stores transcription segments
- [Database models](./libs/shared-models/shared_models/models.py): Data structures for storing meeting information

## Projects Built with Vexa

To see examples of projects built using the Vexa API, including our example client and other community contributions, please see the [BUILT-WITH-VEXA.md](BUILT-WITH-VEXA.md) file.

> 💫 If you're building with Vexa, we'd love your support! [Star our repo](https://github.com/Vexa-ai/vexa/stargazers) to help us reach 1500 stars.

### Features:

- **Real-time multilingual transcription** supporting **99 languages** with **Whisper**
- **Real-time translation** across all 99 supported languages
- (**Note:** Additional features like LLM processing, RAG, and MCP server access are planned - see 'Coming Next')

## Current Status

- **Public API**: Fully available with self-service API keys at [www.vexa.ai](https://www.vexa.ai/?utm_source=github&utm_medium=readme&utm_campaign=vexa_repo)
- **Google Meet Bot:** Fully operational bot for joining Google Meet calls
- **Microsoft Teams Bot:** Supported in v0.6
- **Real-time Transcription:** Low-latency, multilingual transcription service is live
- **Real-time Translation:** Instant translation between 99 supported languages
- **WebSocket Streaming:** Sub-second transcript delivery via WebSocket API
- **Pending:** Speaker identification is under development

## Coming Next

- **Zoom Bot:** Integration for automated meeting attendance (July 2025)
- **Direct Streaming:** Ability to stream audio directly from web/mobile apps
- **Real-time LLM Processing:** Enhancements for transcript readability and features
- **Meeting Knowledge Extraction (RAG):** Post-meeting analysis and Q&A

## Self-Deployment

For **security-minded companies**, Vexa offers complete **self-deployment** options.

To run Vexa locally on your own infrastructure, the primary command you'll use after cloning the repository is `make all`. This command sets up the environment (CPU by default, or GPU if specified), builds all necessary Docker images, and starts the services.

[3 min video tutorial](https://www.youtube.com/watch?v=bHMIByieVek)

Detailed instructions: [Local Deployment and Testing Guide](DEPLOYMENT.md).

## Contributing

Contributors are welcome! Join our community and help shape Vexa's future. Here's how to get involved:

1. **Understand Our Direction**:

   * Check out the **project roadmap** to see where we're headed: [Vexa Project Roadmap](https://github.com/orgs/Vexa-ai/projects/1)
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
