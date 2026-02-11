# Vexa Docs Index

Use this page as the entry point for setup, operations, and API usage.

## Fastest Paths

| Goal | Start Here |
|---|---|
| Run Vexa for local development | [Deployment Guide](deployment.md) |
| Run Vexa Lite in production | [Vexa Lite Deployment Guide](vexa-lite-deployment.md) |
| Manage users and API tokens | [Self-Hosted Management Guide](self-hosted-management.md) |
| Integrate over REST | [User API Guide](user_api_guide.md) |
| Stream live transcripts | [WebSocket Guide](websocket.md) |

## Platform and Integration Docs

- [Zoom App Setup](zoom-app-setup.md): Zoom app configuration (OAuth, Meeting SDK, app review)
- [Zoom App Submission Data](zoom-app-submission-data.md): templates/checklist for app review forms
- [Zoom Basic Info Snapshot](zoom-basic-info-snapshot.md): pre-filled app profile fields
- [Zoom Architecture Diagram](zoom-architecture-diagram.md): Zoom integration flow
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
5. If needed, configure Zoom with [Zoom App Setup](zoom-app-setup.md).

## Support

- Discord: https://discord.gg/Ga9duGkVz9
- Issues: https://github.com/Vexa-ai/vexa/issues
- Website: https://vexa.ai
