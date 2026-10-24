# Vexa plugin for Claude Code

Claude Code plugin for Vexa (meeting bot + live transcripts for Google Meet, Microsoft Teams and Zoom). It brings:

- the hosted Vexa MCP server at `https://api.cloud.vexa.ai/mcp`, signed in with OAuth: no API key;
- a **Vexa pane** beside the chat (`hooks/register.tsx`): it opens at session start, shows the sign-in step until Vexa is connected, then a meeting-link field, **Send bot**, **Try a demo** and the live transcript;
- a permission hook: the pane's own calls and Vexa's read-only tools run without a prompt; anything the model does in a meeting (`speak_in_meeting`, `stop_bot`, `request_meeting_bot`, …) keeps the session's normal prompt;
- two skills: working with meetings, and setup.

Install from this repo:

```
/plugin marketplace add Vexa-ai/vexa
/plugin install vexa@vexa
```

Then sign in: `/mcp` → `plugin:vexa:vexa` → **Authenticate** → sign in with Google or Microsoft → **Allow**. A new account starts with $2 of free credit.

Self-hosted Vexa: see `skills/setup/SKILL.md`.

Tests: `claude plugin validate clients/claude-plugin` and `claude plugin test clients/claude-plugin` (`tests/`).

Docs: https://docs.vexa.ai/vexa-mcp
