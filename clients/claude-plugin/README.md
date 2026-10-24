# Vexa plugin for Claude Code

Claude Code plugin for Vexa (meeting bot + live transcripts for Google Meet, Microsoft Teams and Zoom). It brings:

- the hosted Vexa MCP server at `https://api.cloud.vexa.ai/mcp`, signed in with OAuth: no API key;
- **one Vexa pane** beside the chat (`hooks/register.tsx`): it opens at session start, shows the sign-in step until Vexa is connected, then views switched by buttons at its top: **Home** (send Vexa, try a demo, live now, let Claude listen, ask Claude, recent calls), **Meeting** (the live transcript), **Calls**, **Calendar** (connect, upcoming meetings with an auto-join toggle each) and **Workspaces** (list, find and open a page, bind the current meeting). `/vexa`, `/vexa-meeting`, `/vexa-calls` open it at that view; `/vexa-help` explains it;
- a **listener** for live calls: offered when a meeting is live, on only after **Listen**; a small model wakes Claude when something matters; Claude tunes it with `listener_configure` and measures it with `listener_calibrate`;
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
