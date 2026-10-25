# Vexa for Claude: administrator's page

The plugin runs inside your people's Claude Code. Meetings, transcripts and workspaces live on your own
Vexa. The plugin talks to one Vexa, the address you give it, and nothing else of ours.

## What runs where

| Piece | Runs on | What it is |
|---|---|---|
| Plugin: one MCP server entry | the person's machine, inside Claude Code | `.mcp.json`; no code, no server process of its own |
| Listener's filter model | the person's Claude Code model provider (Anthropic, Bedrock, Vertex, Foundry: whatever Claude Code is set to) | a small-model call per batch of new transcript lines, through Claude Code's own `$.model` |
| MCP server, sign-in, bots, transcription, storage | **your Vexa** | `<Vexa address>/mcp` and the services behind it |

## Setting the address

The plugin has no install-time options. Its MCP server is a literal address in `.mcp.json`
(`mcpServers.vexa.url`, hosted `https://api.cloud.vexa.ai/mcp`); the Claude desktop app's sign-in compares that exact text with the
session's server, so it can't hold variables.

- **Your company's address:** ship the plugin from your own marketplace (below) with `.mcp.json`'s `mcpServers.vexa.url` set to
  `https://vexa.example.com/mcp`. The plugin reads the address from there.
- **Without a mirror:** declare the server yourself in `managed-mcp.json` (a managed server wins over a plugin server).

- **Keep hosted Vexa out:** add `{"deniedMcpServers": [{"serverUrl": "https://api.cloud.vexa.ai/*"}]}` to managed settings,
  or allow only your own with `allowedMcpServers` + `allowManagedMcpServersOnly: true`.
- **Your own marketplace:** mirror `Vexa-ai/vexa` (or just `clients/claude-plugin`) internally and pin it with
  `extraKnownMarketplaces` / `strictKnownMarketplaces`, so installs never reach GitHub.

## Sign-in

No API keys anywhere. Claude Code runs standard MCP OAuth against your Vexa:

1. On the person's yes Claude calls the server's `authenticate` tool (Claude Code's own, offered while sign-in is needed) and gives them the link (or they use `/mcp` → `plugin:vexa:vexa` → **Authenticate**).
2. Claude Code POSTs to `/mcp` and gets `401` with `WWW-Authenticate: Bearer resource_metadata=…` (RFC 9728).
3. It reads `/.well-known/oauth-protected-resource/mcp`, then your authorization server's
   `/.well-known/oauth-authorization-server` (RFC 8414).
4. It identifies itself by its client metadata document (CIMD, `https://claude.ai/oauth/claude-code-client-metadata`),
   or registers dynamically (DCR).
5. It opens the browser at your `/oauth/authorize` with PKCE (S256). Your authorization server sends the person
   through **your SSO** (OIDC: Entra, Okta, ADFS, Keycloak), shows consent, and redirects to Claude Code's
   loopback callback (`http://localhost:<port>/callback`).
6. Claude Code exchanges the code at `/oauth/token`. It keeps the access and refresh tokens in the OS keychain.

Your IdP never talks to Claude Code directly; your Vexa's authorization server fronts it. So an IdP without
DCR (Entra, Okta) needs no client registration for Claude Code. Revoking a person's grant on your Vexa ends their access.

## Every network call

| From | To | When | Carries |
|---|---|---|---|
| Claude Code | `<Vexa address>/mcp` | every tool call, from the model or the plugin | the call's arguments; `Authorization: Bearer <OAuth access token>` |
| Claude Code | `<Vexa address>/.well-known/*`, `/oauth/*` | sign-in and token refresh | PKCE code, refresh token |
| browser | `<Vexa address>` sign-in, your IdP | sign-in only | your SSO session |
| your authorization server | `claude.ai/oauth/claude-code-client-metadata` | during sign-in, to read Claude Code's client metadata (CIMD) | nothing about the person |
| browser | a link Vexa returns, e.g. the calendar page | when the person opens it | that page's own traffic, on your Vexa |
| Claude Code | `github.com/Vexa-ai/vexa` | install and update, unless you mirror the marketplace | nothing about the person |

The plugin makes no other calls. It has no telemetry of its own, and nothing in it reaches a Vexa-run service
unless the address is ours. Claude Code's own telemetry is yours to switch off: set
`DISABLE_TELEMETRY=1` or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` in managed `env`. On Bedrock, Vertex and
Foundry it is off by default. If you want usage data, Claude Code's OpenTelemetry export
(`CLAUDE_CODE_ENABLE_TELEMETRY=1`) sends it to your own collector.

## The data

- **On your Vexa:** meetings, recordings, transcripts, chat, calendar connections, workspaces, OAuth grants.
- **On the person's machine:** OAuth tokens (OS keychain).
- **In the Claude conversation:** whatever transcript or tool result the model reads, under your Claude agreement
  like any other tool output.
- **For audio to stay with you,** run transcription on your own Vexa (`deploy/transcription`). A
  `TRANSCRIPTION_SERVICE_URL` pointing at hosted Vexa sends meeting audio to us.

## What your Vexa must serve

- `/mcp` on an HTTPS address that people's machines can reach, answering `401` with the `resource_metadata`
  challenge, plus `/.well-known/oauth-protected-resource/mcp`.
- An OAuth 2.1 authorization server (authorize, token, revoke, PKCE, CIMD or DCR) whose sign-in is your SSO.

Open-source Vexa doesn't ship the authorization server yet: today it runs in hosted Vexa only. Until it
does, a self-hosted stack works with the plugin only through an edge that adds it.

## Checking it

- `claude mcp list` shows `plugin:vexa:vexa: https://<your address>/mcp`.
- `curl -si -X POST https://<your address>/mcp` returns `401` with a `resource_metadata` challenge.
