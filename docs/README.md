# Vexa Docs

This is the canonical entry point for Vexa setup, operations, and API usage.

## Start Here

Pick the path that matches what you're doing:

- **End-to-end (deploy → token → bot → transcript → playback):** [Getting Started](getting-started.md)
- **Self-host in production (recommended):** [Vexa Lite Deployment Guide](vexa-lite-deployment.md)
- **Local development stack (Docker Compose):** [Deployment Guide](deployment.md)
- **API-first integration:** [User API Guide](user_api_guide.md) + [WebSocket Guide](websocket.md)

## Core Concepts

- [Core Concepts](concepts.md): meeting/bot/session model, transcript timing semantics, recordings, delete semantics

## Platforms

- [Google Meet](platforms/google-meet.md)
- [Microsoft Teams](platforms/microsoft-teams.md)
- [Zoom](platforms/zoom.md)
- [Zoom Integration Setup](zoom-app-setup.md): OAuth + Meeting SDK + OBF flow + approval caveats

## Deployment and Operations

- [Deployment Guide](deployment.md): full stack Docker Compose (dev)
- [Vexa Lite Deployment Guide](vexa-lite-deployment.md): single container (prod self-host)
- [Self-Hosted Management Guide](self-hosted-management.md): users + tokens + admin workflows
- [Recording Storage Modes](recording-storage.md): local vs MinIO vs S3-compatible; playback and `Range`/`206` behavior

## UI (Dashboard)

- [Vexa Dashboard](ui-dashboard.md): run the UI and use post-meeting playback

## Troubleshooting and Security

- [Troubleshooting](troubleshooting.md)
- [Security and Data Handling](security.md)

## Voice Agent (Meeting Interaction)

- [Voice Agent Guide](voice-agent.md): make the bot speak, chat, and share content in meetings (TTS, chat read/write, screen share)

## Misc / Integrations

- [ChatGPT Transcript Share Links](chatgpt-transcript-share-links.md): shared transcript URL behavior

## Notebooks (`../nbs`)

- `0_basic_test.ipynb`: end-to-end bot lifecycle smoke test
- `1_load_tests.ipynb`: load testing scenarios
- `2_bot_concurrency.ipynb`: concurrent bot behavior
- `3_API_validation.ipynb`: API endpoint validation
- `manage_users.ipynb`: user and token management examples

## Typical Developer Flow

1. Deploy locally with [Deployment Guide](deployment.md).
2. Create users/tokens with [Self-Hosted Management Guide](self-hosted-management.md).
3. Integrate REST endpoints via [User API Guide](user_api_guide.md).
4. Add live updates with [WebSocket Guide](websocket.md).
5. If needed, configure Zoom with [Zoom Integration Setup](zoom-app-setup.md).
6. For interactive bots (speak, chat, screen share), see [Voice Agent Guide](voice-agent.md).

## Support

- Discord: https://discord.gg/Ga9duGkVz9
- Issues: https://github.com/Vexa-ai/vexa/issues
- Website: https://vexa.ai
