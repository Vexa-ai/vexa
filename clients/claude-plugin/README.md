# Vexa plugin for Claude Code

Claude Code plugin that connects the Vexa MCP server (meeting bot + transcripts for Google Meet, Microsoft Teams and Zoom) and ships two skills: working with meetings, and setup.

Install from this repo:

```
/plugin marketplace add Vexa-ai/vexa
/plugin install vexa@vexa
```

Configuration: a Vexa API key (hosted: https://vexa.ai/account; self-hosted: printed by `make all`), and optionally the MCP URL for a self-hosted stack.

Docs: https://docs.vexa.ai/vexa-mcp

## What it connects to

The plugin runs no code of its own. It declares one MCP server: `https://api.cloud.vexa.ai/mcp` (hosted Vexa), or the URL you set for a self-hosted stack. Claude Code sends that server your API key and the tool calls Claude makes: meeting links, bot requests, transcript and meeting queries, and anything you ask the bot to say in a meeting. The bot joins the meetings you send it to and records and transcribes them; transcripts are stored in your Vexa account (hosted) or on your own stack (self-hosted).

## Privacy

Hosted Vexa: https://vexa.ai/legal/privacy. With a self-hosted URL, the plugin talks only to your own stack.
