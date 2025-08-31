# Vexa Meeting Bot MCP Setup Guide

Welcome! This guide will help you set up and connect Claude (or any other client) to the Vexa Meeting Bot MCP (Model Context Protocol).
Follow these steps carefully, even if you are new to these tools. In under 5 minutes you will be easily set up. All we have to do is install Node.js and copy paste a config.

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
        "Authorization:${VEXA_API_KEY}"
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

## Troubleshooting

- If you see errors about missing `npx` or `npm`, make sure Node.js is installed
- If you get authentication errors, double-check your API key
- For further help, contact Vexa support

---

**For more information about the Vexa API , visit:** [https://vexa.ai](https://vexa.ai)