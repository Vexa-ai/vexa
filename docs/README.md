# Vexa Documentation

Vexa is an open-source meeting bot + API for real-time transcription and post-meeting recording & playback.

If you're here, you likely want one of three things:

- **Get transcripts via API** — use the hosted service to start in minutes
- **Self-host Vexa** — full control over your data and deployment
- **Use the dashboard** — open-source UI to join meetings, review history, and play recordings

## How It Works

1. **Send a bot to a meeting** — Use `POST /bots` with `platform` + `native_meeting_id` (and `passcode` when required).
2. **Stream or fetch transcripts** — Use WebSockets for live updates or `GET /transcripts/{platform}/{native_meeting_id}` for the full result.
3. **Stop, then review post-meeting artifacts** — When the meeting ends, recordings (if enabled) become available for playback.
4. **Optionally delete/anonymize** — Delete transcript + recording artifacts with `DELETE /meetings/{platform}/{native_meeting_id}`.

## Choose Your Path

### [Hosted API](https://vexa.ai) (recommended to start)

Use the Vexa Cloud API and dashboard — no infrastructure to manage.

1. Get an API key from [vexa.ai/dashboard/api-keys](https://vexa.ai/dashboard/api-keys)
2. Send a bot: [`POST /bots`](user_api_guide.md)
3. Read transcripts: [`GET /transcripts/{platform}/{native_meeting_id}`](user_api_guide.md)
4. For live streaming: [WebSocket guide](websocket.md)

### [Self-Hosted (Vexa Lite)](vexa-lite-deployment.md) (full control)

Self-host to have full control over your data and deployment. Vexa Lite is a single container that connects to your Postgres database and a remote transcription service.

### [Docker Compose (dev)](deployment.md)

Full local stack for contributors and development/testing.

### [Vexa Dashboard](ui-dashboard.md) (open-source UI)

Open-source Next.js dashboard for joining meetings, viewing live transcripts, and reviewing history. Fork it or use it as a reference for building your own integration.

- Repo: [github.com/Vexa-ai/Vexa-Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard)

## Core Concepts

- [Core concepts](concepts.md): bot/meeting/session model + timing semantics
- [Recording & storage](recording-storage.md): how artifacts are stored + playback notes (`Range`/`206`)
- [Delete semantics](concepts.md#delete-semantics): what "delete" means and what remains for telemetry

## Zoom (The Only Special Platform)

Zoom has extra constraints (OAuth + Meeting SDK + OBF token flow) and typically Marketplace approval.

- [Zoom limitations](platforms/zoom.md)
- [Zoom app setup](zoom-app-setup.md)

## Troubleshooting / Security

- [Troubleshooting](troubleshooting.md)
- [Security](security.md)

## Voice Agent (Meeting Interaction)

- [Voice Agent Guide](voice-agent.md): make the bot speak, chat, and share content in meetings (TTS, chat read/write, screen share)

## Integrations

- [Integrations](integrations.md)
- [ChatGPT transcript share links](chatgpt-transcript-share-links.md)

## Typical Developer Flow

1. Deploy locally with [Deployment Guide](deployment.md).
2. Create users/tokens with [Self-Hosted Management Guide](self-hosted-management.md).
3. Integrate REST endpoints via [User API Guide](user_api_guide.md).
4. Add live updates with [WebSocket Guide](websocket.md).
5. If needed, configure Zoom with [Zoom Integration Setup](zoom-app-setup.md).
6. For interactive bots (speak, chat, screen share), see [Voice Agent Guide](voice-agent.md).

## Support / Roadmap

- Issues: https://github.com/Vexa-ai/vexa/issues
- Milestones: https://github.com/Vexa-ai/vexa/milestones
- Discord: https://discord.gg/Ga9duGkVz9
