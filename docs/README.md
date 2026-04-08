# Vexa Documentation

Public documentation served at [docs.vexa.ai](https://docs.vexa.ai). Built with Mintlify using `.mdx` files.

## Structure

- `docs.json` controls navigation (tabs: Docs, API Reference; groups: Start Here, Deploy, Dashboard, Admin, Concepts, Platforms, Features, Guides)
- 28+ `.mdx` pages across `docs/`, `docs/api/`, `docs/platforms/`
- `.mdx` is the single source of truth -- no `.md` duplicates
- GA4: `G-45M7REZYT1`, SEO canonical: `https://docs.vexa.ai`
- Feature maturity labels: `stable`, `beta`, `experimental`
- Assets: `assets/logodark.svg`, `assets/logo.svg`

## Page ownership

Source of truth: `tests3/docs/registry.json` (machine-readable, validated by `make docs`).

Each service owns its documentation pages. The docs structure owns navigation, cross-links, and consistency.

| Service | README | Docs pages |
|---------|--------|------------|
| api-gateway | services/api-gateway/README.md | quickstart, getting-started, errors-and-retries, websocket, token-scoping, security, user_api_guide |
| meeting-api | services/meeting-api/README.md | bot-overview, api/bots, interactive-bots, api/interactive-bots |
| vexa-bot | services/vexa-bot/README.md | bot-overview, meeting-ids, platforms/google-meet, platforms/microsoft-teams, platforms/zoom |
| transcription-collector | services/transcription-collector/README.md | api/transcripts, api/meetings |
| transcription-service | services/transcription-service/README.md | concepts, recording-storage |
| admin-api | services/admin-api/README.md | self-hosted-management, api/settings |
| dashboard | services/dashboard/README.md | ui-dashboard, zoom-app-setup |
| mcp | services/mcp/README.md | vexa-mcp |
| shared-models | libs/shared-models/README.md | webhooks, token-scoping |
| deploy/lite | deploy/lite/README.md | vexa-lite-deployment |
| deploy/compose | deploy/compose/README.md | deployment |
| deploy/helm | deploy/helm/README.md | deployment |

Pages not owned by a specific service: index, integrations, local-webhook-development, chatgpt-transcript-share-links, troubleshooting.

## Consistency checks

When updating documentation, verify these three directions:

| Direction | What to check |
|-----------|---------------|
| README -> code | Every claim in the README (endpoints, ports, env vars, defaults) matches current code |
| Code -> README | Every user-facing behavior in code is documented in the README |
| README -> docs | Links resolve; shared claims (auth, URLs, params) don't contradict between README and docs page |
