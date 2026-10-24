# Vexa plugin for Claude Code

Claude Code plugin for Vexa (meeting bot + live transcripts for Google Meet, Microsoft Teams and Zoom). It brings:

- the Vexa MCP server at `<Vexa address>/mcp`, signed in with OAuth against that Vexa's own sign-in: no API key. The address is the plugin's `vexa_url` option, hosted Vexa (`https://api.cloud.vexa.ai`) unless set;
- **one Vexa pane** beside the chat (`hooks/register.tsx`): it opens at session start, shows the sign-in step until Vexa is connected, then views switched by buttons at its top: **Home** (send Vexa, try a demo, live now, let Claude listen, ask Claude, recent calls), **Meeting** (the live transcript), **Calls**, **Calendar** (connect, upcoming meetings with an auto-join toggle each) and **Workspaces** (list, find and open a page, bind the current meeting). `/vexa`, `/vexa-meeting`, `/vexa-calls` open it at that view; `/vexa-help` explains it;
- a **listener** for live calls: offered when a meeting is live, on only after **Listen**; a small model wakes Claude when something matters; Claude tunes it with `listener_configure` and measures it with `listener_calibrate`;
- a permission hook: the pane's own calls and Vexa's read-only tools run without a prompt; anything the model does in a meeting (`speak_in_meeting`, `stop_bot`, `request_meeting_bot`, …) keeps the session's normal prompt;
- setup in the chat, not in the pane: the session-start context tells Claude what is connected and which setup question comes next («Where should I keep what I learn: on this computer, or in a cloud workspace you can share?»); the answer is saved with the plugin's `vexa_settings` tool (cloud also sets the account's «Writes to …» default, shared with the web);
- after a real call ends, when writing is allowed, Claude files it into the chosen workspace by that workspace's own rules and answers with one line and a link;
- three skills: working with meetings, setup, and keeping a workspace (`vexa-workspace`).

## What it connects to and what it does on its own

- **One server:** the MCP server of the Vexa named by the **Vexa address** option (`vexa_url`, hosted `https://api.cloud.vexa.ai` by default; a company's own Vexa otherwise — see `ADMIN.md`), signed in with OAuth through Claude Code's `/mcp`. The plugin holds no key; the pane calls the same server through Claude Code.
- **Permissions it grants itself (`tool.check`):** the pane's own button presses; Vexa's read-only tools (list, get, search, transcript, calendar status, workspace list/read/search); and its own three tools (`listener_configure`, `listener_calibrate`, `vexa_settings`). `speak_in_meeting` and `stop_bot` always ask the person; every other Vexa write keeps the session's normal prompt.
- **Model calls:** only while the person has pressed **Listen** on a meeting, each change in that meeting's transcript is sent to a small Claude model (haiku) through Claude Code's own model access, with the listen rules, to decide whether to wake the main Claude. Nothing else of the session is sent.
- **Turns it starts:** a listener wake, the «pressed Listen» note, «What did I miss?», a finished call being filed (only when the person allowed writing), and requests the person queued from Vexa's web panel.
- **Files:** it copies its default app (pane copy, listen presets) to `~/.vexa/apps/meeting-assistant/` on first run; a project's `.vexa/app/` overrides it. Workspace pages are written by Claude through the server, or under `~/Vexa` when the person chose "this computer".
- **Telemetry:** none in the plugin; hosted Vexa's server counts sign-ups and tool calls (vexa.ai privacy policy); a company's own Vexa keeps everything on its server.

Install from this repo:

```
/plugin marketplace add Vexa-ai/vexa
/plugin install vexa@vexa
```

Then sign in: `/mcp` → `plugin:vexa:vexa` → **Authenticate** → sign in → **Allow**. On hosted Vexa that is Google or Microsoft, and a new account starts with $2 of free credit.

Your company's own Vexa: set the address at install (`claude plugin install vexa@vexa --config vexa_url=https://vexa.example.com`) or later (`/plugin configure vexa@vexa`); the sign-in is then your company's. Administrators: [`ADMIN.md`](ADMIN.md) covers pushing the address through managed settings, what runs where, every network call and the data.

Tests: `claude plugin validate clients/claude-plugin` and `claude plugin test clients/claude-plugin` (`tests/`).

Docs: https://docs.vexa.ai/vexa-mcp
