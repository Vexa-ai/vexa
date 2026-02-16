# Vexa Meeting Bot MCP Setup Guide

Welcome! This guide will help you set up and connect Claude (or any other client) to the Vexa Meeting Bot MCP (Model Context Protocol).
Follow these steps carefully, even if you are new to these tools. In under 5 minutes you will be easily set up. All we have to do is install Node.js and copy paste a config.

## Teams Passcodes and URL Limitations (Important)

Vexa can join Microsoft Teams meetings, but **Teams meeting links are tricky** and **many meetings require a passcode**.

Key points:

- **Only Teams Free style links are supported**: `https://teams.live.com/meet/<MEETING_ID>?p=<PASSCODE>`
- **Recommended:** pass the **full Teams URL** via `meeting_url` (Vexa will parse out `native_meeting_id` + `passcode` for you).
- If you prefer passing parts separately:
  - `native_meeting_id`: the numeric `<MEETING_ID>` (10-15 digits)
  - `passcode`: the `<PASSCODE>` from `?p=...` (often required)
- **Full Teams URLs are not accepted** as `native_meeting_id`. Use `meeting_url` or the numeric ID only.
- **`teams.microsoft.com/l/meetup-join/...` links are not supported yet** (see issues #105, #110). If you have one of these links, you must obtain a `teams.live.com/meet/...` link instead (or use the REST API with the numeric ID + passcode if you already know them).
- **Passcode constraints**: Teams passcodes must be **8-20 alphanumeric characters**. If your `p=` value contains non-alphanumeric characters or is longer than 20, it will be rejected.

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

You will need your Vexa API key to connect to the MCP. If you do not have one, please generate it or view existing ones from https://vexa.ai/dashboard/api-keys

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
        "Authorization: Bearer ${VEXA_API_KEY}"
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

Meeting preparation:

- `parse_meeting_link`: paste a full meeting URL to extract `platform`, `native_meeting_id`, and `passcode` (Teams/Zoom).
- `update_meeting_data`: set `name`, `participants`, `languages`, and `notes` ahead of time (these notes are surfaced in transcript responses).

During the meeting:

- `get_bot_status`: see which bots are currently running.
- `get_meeting_transcript`: fetch the current transcript snapshot (REST-style polling).

Post meeting:

- `create_transcript_share_link`: create a short-lived public URL for a transcript (good for sharing / downstream tools).
- `get_meeting_bundle`: one call to fetch status + notes + recordings + (optional) share link.
- Recordings:
  - `list_recordings`, `get_recording`, `get_recording_media_download`, `delete_recording`
  - `get_recording_media_download` returns an absolute `download_url` when running on local storage.

## Troubleshooting

- If you see errors about missing `npx` or `npm`, make sure Node.js is installed
- If you get authentication errors, double-check your API key
- If Teams meetings fail to join, verify you are using a `teams.live.com/meet/...` link and that you extracted both the numeric meeting ID and the `?p=` passcode.
- For further help, contact Vexa support

---

**For more information about the Vexa API , visit:** [https://vexa.ai](https://vexa.ai)
