<p align="left">
  <img src="assets/logodark.svg" alt="Vexa Logo" width="40"/>
</p>

# Vexa: API for **Real-Time Meeting Transcription**

Vexa is an API for **real-time meeting transcription** using **meeting bots** and direct **streaming from web/mobile apps**. It extracts knowledge from various platforms including:

- **Google Meet**
- **Zoom** (coming soon)
- **Microsoft Teams** (coming soon)

Built with a **scalable architecture**, Vexa is designed to support **thousands of simultaneous users** and **concurrent transcription sessions**. It serves as an **enterprise-grade** alternative to [recall.ai](https://recall.ai) with numerous extra features, developed with **secure corporate environments** in mind where **data security** and **compliance** are crucial.

## 🎉 Major Release: Public API Now Available!

The Vexa API is now **publicly available** at [vexa.ai](https://vexa.ai) with **self-service access** - get your API key in just 3 clicks and have everything running in under 5 minutes.

### Key features in this release:

- **Instant API Access**: Self-service API keys available directly from [vexa.ai](https://vexa.ai)
- **Google Meet Bot Integration**: Programmatically send bots to join and transcribe meetings
- **Real-Time Transcription**: Access meeting transcripts as they happen through the API
- **Real-Time Translation**: Change the language of transcription to get instant translations across 99 languages



## API Capabilities


## Simple API Integration
**Set up and running in under 5 minutes**

Get your API key in 3 clicks at [vexa.ai](https://vexa.ai) and start using the API immediately.

### Create a meeting bot
```bash
# POST /bots
curl -X POST https://gateway.dev.vexa.ai/bots \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "native_meeting_id": "xxx-xxxx-xxx",
    "platform": "google_meet"
  }'
```

### Retrieve meeting transcript
```bash
# GET /transcripts/{platform}/{native_meeting_id}
# Example assumes native_meeting_id is derived from the meeting URL
curl -H "X-API-Key: YOUR_CLIENT_API_KEY" \
  https://gateway.dev.vexa.ai/transcripts/google_meet/xxx-xxxx-xxx
```

```json
{
  "data": {
    "meeting_id": "meet_abc123",
    "transcripts": [
      {
        "time": "00:01:15",
        "speaker": "John Smith",
        "text": "Let's discuss the quarterly results."
      },
      {
        "time": "00:01:23",
        "speaker": "Sarah Johnson",
        "text": "The Q3 revenue exceeded our projections by 15%."
      },
      {
        "time": "00:01:42",
        "speaker": "Michael Chen",
        "text": "Customer acquisition costs decreased by 12% from last quarter."
      }
    ]
  }
}
```

### Inputs:
- **Meeting Bots**: Automated bots that join your meetings on:
  - Google Meet
  - Zoom
  - Microsoft Teams
  - And more platforms

- **Direct Streaming**: Capture audio directly from:
  - Web applications
  - Mobile apps

### Features:
- **Real-time multilingual transcription** supporting **99 languages** with **Whisper**
- **Real-time translation** across all 99 supported languages
- (**Note:** Additional features like LLM processing, RAG, and MCP server access are planned - see 'Coming Next')

## Scalability Architecture Overview

Vexa is designed from the ground up as a **high-performance, scalable multiuser service** using a microservice-based architecture allowing independent scaling of components and distributed processing.
*(For architecture details relevant to deployment, see [DEPLOYMENT.md](DEPLOYMENT.md))*.

## Current Status

- **Public API**: Fully available with self-service API keys at [vexa.ai](https://vexa.ai)
- **Google Meet Bot:** Fully operational bot for joining Google Meet calls
- **Real-time Transcription:** Low-latency, multilingual transcription service is live
- **Real-time Translation:** Instant translation between 99 supported languages
- **Pending:** Speaker identification is under development

## Coming Next

- **Microsoft Teams Bot:** Integration for automated meeting attendance (April 2025)
- **Zoom Bot:** Integration for automated meeting attendance (May 2025)
- **Direct Streaming:** Ability to stream audio directly from web/mobile apps
- **Real-time LLM Processing:** Enhancements for transcript readability and features
- **Meeting Knowledge Extraction (RAG):** Post-meeting analysis and Q&A
- **MCP Server:** Access to transcription data for agents

## Self-Deployment

For **security-minded companies**, Vexa offers complete **self-deployment** options.

Detailed instructions for setting up a local development environment or deploying the system yourself can be found in [DEPLOYMENT.md](DEPLOYMENT.md).

## Contributing

Contributors are welcome! Join our community and help shape Vexa's future:

- **Research & Discuss**:
  - Review our **roadmap** in the [Project Tasks Board](https://github.com/Vexa-ai/vexa/projects)
  - Join discussions in our [Discord Community](https://discord.gg/Ga9duGkVz9)
  - Share your ideas and feedback

- **Get Involved**:
  - Browse available **tasks** in our task manager
  - Request task assignment through Discord
  - Submit **pull requests** for review

- **Critical Tasks**:
  - Selected **high-priority tasks** will be marked with **bounties**
  - Bounties are sponsored by the **Vexa core team**
  - Check task descriptions for bounty details and requirements

To contribute:
1. Join our Discord community
2. Review the roadmap and available tasks
3. Request task assignment
4. Submit a pull request

## Project Links

- 🌐 [Vexa Website](https://vexa.ai)
- 💼 [LinkedIn](https://www.linkedin.com/company/vexa-ai/)
- 🐦 [X (@grankin_d)](https://x.com/grankin_d)
- 💬 [Discord Community](https://discord.gg/Ga9duGkVz9)

## License

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

Vexa is licensed under the **Apache License, Version 2.0**. See [LICENSE](LICENSE) for the full license text.

The Vexa name and logo are trademarks of **Vexa.ai Inc**. See [TRADEMARK.md](TRADEMARK.md) for more information.
