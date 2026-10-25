# Vexa plugin for Claude Code

Vexa (meeting bot + live transcripts for Google Meet, Microsoft Teams and Zoom) as one MCP server, nothing else:
`.mcp.json` points Claude at Vexa's `/mcp`, signed in with your Vexa account (OAuth). No API key, no options.

What Claude does next — the first demo, sending a bot, your calls panel — comes from Vexa's server, so it
improves without a plugin update.

Install:

```
claude plugin install vexa --marketplace Vexa-ai/vexa
```

Then sign in: in the terminal Claude gives you the link; in the Claude desktop app type `/mcp`, choose
`plugin:vexa:vexa` and sign in. A company running its own Vexa: see `ADMIN.md`.
