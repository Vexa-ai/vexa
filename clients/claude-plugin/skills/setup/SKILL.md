---
name: vexa-setup
description: Set up or fix the Vexa connection for this plugin — signing in, what a new account gets, self-hosted Vexa, and how to check the connection. Use when the vexa server is missing, asks for sign-in, returns 401, or the user asks how to connect Vexa.
---

# Vexa setup

The plugin connects to hosted Vexa at `https://api.cloud.vexa.ai/mcp` and signs in with OAuth. There is no API key to paste.

- **Sign in**: run `/mcp`, pick `plugin:vexa:vexa`, choose **Authenticate**. The browser opens vexa.ai; the user signs in with Google or Microsoft and presses **Allow**. A new account is created on first sign-in and starts with $2 of free credit. With no browser (SSH), Claude Code prints a URL to open elsewhere and paste back.
- **The Vexa pane** opens beside the chat at session start (`/vexa` reopens it). It shows the sign-in step until the connection is up, then a meeting-link field, **Send bot** and **Try a demo**.
- **No meeting right now**: **Try a demo** in the pane (or the `start_demo_meeting` tool) starts a Vexa-hosted room where a presenter speaks for about a minute, so the user sees a live transcript without a link or admitting a bot.
- **Self-hosted Vexa**: this plugin always talks to hosted Vexa. For a self-hosted stack, add the server yourself with the key `make all` prints: `claude mcp add --transport http vexa http://localhost:18056/mcp --header "Authorization: Bearer <key>"`. Docs: https://docs.vexa.ai/deployment

Check the connection by calling `whats_waiting`. A sign-in prompt or 401 means the OAuth sign-in hasn't happened or was revoked (vexa.ai/account → Connected apps); a connection error means the server can't be reached.

Never ask the user to paste a key or token into the chat.
