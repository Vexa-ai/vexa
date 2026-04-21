# Vexa MCP Service

## Why

AI assistants (Claude, Cursor, etc.) need a structured way to interact with Vexa — launching bots, fetching transcripts, managing recordings, chatting in meetings, speaking via TTS, managing calendar — without building custom integrations. The Model Context Protocol (MCP) provides a standard tool interface that any MCP-compatible client can use. Without this service, every AI client would need its own Vexa API integration code.

## What

A FastAPI service that exposes Vexa's meeting capabilities as **32+ MCP tools**, **4+ prompts**, and **MCP Resources** with a custom `vexa://` URI scheme. It proxies to the api-gateway, translating MCP tool calls into Vexa API requests.

### Documentation
- [Vexa MCP](../../docs/vexa-mcp.mdx)

### Tool Categories

| Category | Count | Examples |
|----------|-------|---------|
| Meeting Management | 7 | `request_meeting_bot`, `stop_bot`, `list_meetings`, `parse_meeting_link` |
| Transcripts & Sharing | 3 | `get_meeting_transcript`, `get_meeting_bundle`, `create_transcript_share_link` |
| Recordings | 6 | `list_recordings`, `get_recording`, `get_recording_media_download` |
| Bot Config | 1 | `update_bot_config` |
| Interactive Bot Control | 7 | `send_chat_message`, `read_chat_messages`, `bot_speak`, `stop_speaking`, `bot_screen_share`, `stop_screen_share`, `set_bot_avatar` |
| Calendar | 5 | `calendar_connect`, `calendar_status`, `list_calendar_events`, `update_calendar_preferences` |
| Webhook & Processing | 2 | `configure_webhook`, `transcribe_recording` |

### MCP Protocol Features Used

| Feature | Status | Details |
|---------|--------|---------|
| Tools | Active | 32+ tools with annotations |
| Prompts | Active | 7 workflow prompts |
| Resources | New | `vexa://` URI scheme for meetings/transcripts |
| Tool Annotations | New | readOnly, destructive, idempotent, openWorld hints |
| Subscriptions | Planned | Live transcript push via WebSocket bridge |
| Sampling | Planned | Real-time meeting intelligence |

### Dependencies

- **api-gateway** — all Vexa operations are proxied through the gateway
- No database, no Redis — stateless proxy

## How

See the setup guide below for connecting MCP clients.

---

# Setup Guide

Welcome! This guide will help you set up and connect Claude (or any other client) to the Vexa Meeting Bot MCP (Model Context Protocol).
Follow these steps carefully, even if you are new to these tools. In under 5 minutes you will be easily set up. All we have to do is install Node.js and copy paste a config.

## Teams URL Formats (Updated 2026-04-05)

Vexa can join Microsoft Teams meetings using **all major URL formats**. The `parse_meeting_link` tool handles URL parsing automatically.

**Supported formats (all tested 2026-04-05):**

| Format | Example | Status |
|--------|---------|--------|
| T1: Standard join | `teams.microsoft.com/l/meetup-join/19%3ameeting_{id}%40thread.v2/...` | PASS |
| T2: Meet shortlink (OeNB) | `teams.microsoft.com/meet/{id}?p={passcode}` | PASS |
| T3: Channel meeting | `teams.microsoft.com/l/meetup-join/19%3a{channel}%40thread.tacv2/...` | PASS |
| T4: Custom domain | `{org}.teams.microsoft.com/meet/{id}?p={passcode}` | PASS |
| T5: Deep link | `msteams:/l/meetup-join/...` | NOT SUPPORTED |
| T6: Personal/consumer | `teams.live.com/meet/{id}` | PASS |

**Recommended:** Pass the **full Teams URL** via `meeting_url` — Vexa will parse out `native_meeting_id` + `passcode` automatically.

If you prefer passing parts separately:
- `native_meeting_id`: the numeric meeting ID
- `passcode`: the `<PASSCODE>` from `?p=...` (often required for anonymous join)

**Passcode constraints**: Teams passcodes must be **8-20 alphanumeric characters**. If your `p=` value contains non-alphanumeric characters or is longer than 20, it will be rejected.

## 1. Install Node.js (Required for npm)

The MCP uses `npm` (Node Package Manager) to connect to the server, which comes with Node.js. If you do not have Node.js installed, install it form here, only takes a couple seconds:

- Go to the [Node.js download page](https://nodejs.org/)
- Download the **LTS** (Long Term Support) version for your operating system (Windows, Mac, or Linux)
- Run the installer and follow the prompts
- After installation, open a terminal (Command Prompt, PowerShell, or Terminal) and run:

```
node -v
npm -v
```

You should see version numbers for both. If you do, you are ready to proceed.

## 2. Prepare Your API Key

You will need your Vexa API key to connect to the MCP. If you do not have one, please generate it or view existing ones from https://vexa.ai/account

## 3. Configure Claude to Connect to Vexa MCP
(Same steps can be followed to connect to any other MCP Client (Cursor etc..) make sure you use the same config)


1. **Open Claude Desktop Settings**
   - Launch Claude Desktop
   - Navigate to **Settings** → **Developer**
   - Click **Edit Config** (This will open a file in a text editor such as notepad)


2. **Add MCP Server Configuration**

**Paste the following configuration into your the claude config file you just opened:**

```json
{
  "mcpServers": {
    "fastapi-mcp": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://api.cloud.vexa.ai/mcp",
        "--header",
        "X-API-Key: ${VEXA_API_KEY}"
      ],
      "env": {
        "VEXA_API_KEY": "YOUR_API_KEY_HERE"
      }
    }
  }
}
```

- **Important:** Replace `YOUR_API_KEY_HERE` with your real Vexa API key. Do not share your API key with others.


## 4. Start Using the MCP

Once you have completed the above steps:

- Save your configuration file
- Restart Claude
- Go to developer settings again and ensure that MCP server is there and running
- Start using it

## Useful MCP Tools by Use Case

**Meeting preparation:**
- `parse_meeting_link`: paste a full meeting URL to extract `platform`, `native_meeting_id`, and `passcode` (Teams/Zoom).
- `update_meeting_data`: set `name`, `participants`, `languages`, and `notes` ahead of time.
- `list_calendar_events`: see upcoming meetings from connected calendar.

**During the meeting:**
- `get_bot_status`: see which bots are currently running.
- `get_meeting_transcript`: fetch the current transcript snapshot.
- `send_chat_message`: send a message to the meeting chat.
- `read_chat_messages`: read messages from the meeting chat.
- `bot_speak`: make the bot speak using TTS.

**Post meeting:**
- `create_transcript_share_link`: create a short-lived public URL for a transcript.
- `get_meeting_bundle`: one call to fetch status + notes + recordings + share link.
- `transcribe_recording`: trigger post-meeting transcription of a recording.
- Recordings: `list_recordings`, `get_recording`, `get_recording_media_download`, `delete_recording`

**Configuration:**
- `configure_webhook`: set up webhook notifications for meeting events.
- `calendar_connect` / `calendar_status`: manage calendar integration.
- `update_recording_config`: toggle recording on/off, set capture modes.

## Troubleshooting

- If you see errors about missing `npx` or `npm`, make sure Node.js is installed
- If you get authentication errors, double-check your API key
- If Teams meetings fail to join, verify the URL format is supported (T1-T4, T6 — see table above). Use `parse_meeting_link` to test URL parsing. Ensure passcode is included for anonymous join.
- For further help, contact Vexa support

---

**For more information about the Vexa API , visit:** [https://vexa.ai](https://vexa.ai)

## DoD

| # | Check | Weight | Ceiling | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|--------|----------|--------------|-------|
| 1 | Service starts and MCP tool list discoverable via client | 20 | ceiling | untested | — | — | — |
| 2 | `request_meeting_bot` tool creates bot via api-gateway proxy | 20 | ceiling | untested | — | — | — |
| 3 | `get_meeting_transcript` tool returns transcript for valid meeting | 20 | — | untested | — | — | — |
| 4 | `parse_meeting_link` correctly extracts platform, native_meeting_id, passcode | 15 | — | untested | — | — | — |
| 5 | api-gateway reachable (stateless proxy, no DB/Redis) | 15 | ceiling | untested | — | — | — |
| 6 | MCP prompts and resources return valid responses | 10 | — | untested | — | — | — |

Confidence: 0 (untested — no tests3 checks, no feature coverage)
