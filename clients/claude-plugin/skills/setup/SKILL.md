---
name: vexa-setup
description: Set up or fix the Vexa MCP connection for this plugin — where the API key comes from, hosted vs self-hosted URL, and how to check the connection. Use when the vexa server is missing, returns 401, or the user asks how to connect Vexa.
---

# Vexa setup

The plugin needs one value: **Vexa API key** (`vexa_api_key`), and optionally the **MCP URL** (`vexa_mcp_url`).

- **Hosted**: sign in at https://vexa.ai/account and create a key. URL stays `https://api.cloud.vexa.ai/mcp`.
- **Self-hosted**: `make all` in the Vexa repo prints a key when the stack is up; set the URL to your gateway's `/mcp` (Docker Compose default `http://localhost:18056/mcp`). Docs: https://docs.vexa.ai/deployment
- **The door**: https://vexa.ai/connect mints a key and registers the server for the user's agent without this plugin; either path works.

Check the connection by calling `whats_waiting`. A 401 means the key is missing or wrong; a connection error usually means the URL points at a stack that is not up.

Do not paste keys into chat or files. Tell the user to enter the key in the plugin's settings (it is stored in the OS keychain).
