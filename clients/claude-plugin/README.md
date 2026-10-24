# Vexa plugin for Claude Code

Claude Code plugin for Vexa (meeting bot + live transcripts for Google Meet, Microsoft Teams and Zoom). It brings:

- the Vexa MCP server at `<Vexa address>/mcp`, signed in with OAuth against that Vexa's own sign-in: no API key. hosted Vexa (`https://api.cloud.vexa.ai`), or a company's own address in its mirrored copy;
- **no UI of its own** besides the band above the prompt: `/vexa` (and a call Claude starts) opens Vexa's web panel in Claude's browser pane, with the person's permission; everything else happens in the chat;
- a **listener** for live calls: offered when a meeting is live, on only after **Listen**; a small model wakes Claude when something matters; Claude tunes it with `listener_configure` and measures it with `listener_calibrate`;
- a permission hook: the plugin's own calls and Vexa's read-only tools run without a prompt; anything the model does in a meeting (`speak_in_meeting`, `stop_bot`, `request_meeting_bot`, …) keeps the session's normal prompt;
- setup in the chat: the session-start context tells Claude what is connected and which setup question comes next («Where should I keep what I learn: on this computer, or in a cloud workspace you can share?»); the answer is saved with the plugin's `vexa_settings` tool (cloud also sets the account's «Writes to …» default, shared with the web);
- after a real call ends, when writing is allowed, Claude files it into the chosen workspace by that workspace's own rules and answers with one line and a link;
- three skills: working with meetings, setup, and keeping a workspace (`vexa-workspace`).

## What it connects to and what it does on its own

- **One server:** the MCP server of the Vexa at the literal address in the manifest (hosted `https://api.cloud.vexa.ai`; a company's own Vexa in its own marketplace copy — see `ADMIN.md`), signed in with OAuth through Claude Code's `/mcp`. The plugin holds no key; its own calls go to the same server through Claude Code.
- **Permissions it grants itself (`tool.check`):** its own calls (band presses, the listener); Vexa's read-only tools (list, get, search, transcript, calendar status, workspace list/read/search); and its own three tools (`listener_configure`, `listener_calibrate`, `vexa_settings`). `speak_in_meeting` and `stop_bot` always ask the person; every other Vexa write keeps the session's normal prompt.
- **Model calls:** only while the person has pressed **Listen** on a meeting, through Claude Code's own model access, three jobs, each on its own model (changed from chat with `listener_configure` «models»): the **filter** (haiku: wake or skip, plus the trigger in ≤3 words) reads each change in that meeting's transcript; the **writer** (sonnet) merges new speech into the call's map within ~15 s of a filter hit, plus a sweep every 2 min; the **triage** (sonnet) decides whether a signal that isn't a direct address deserves the person now. Only a direct address or a triage yes reaches the main chat. `listener_configure` status reports each job's calls and tokens per listened hour. Nothing else of the session is sent.
- **Writes it makes on its own:** while listening (unless the person said «only when I ask»), the call's README (≤1500 characters) goes onto the meeting's notes page, or `calls/<date>-<id>.md` in the person's **personal** workspace, and the people and companies it names become linked cards there (`entity_upsert`), by the workspace's own `CLAUDE.md` rules.
- **Turns it starts:** a listener wake, the «pressed Listen» note, «What did I miss?», a finished call being filed (only when the person allowed writing), and requests the person queued from Vexa's web panel.
- **Files:** it copies its default app (listen presets) to `~/.vexa/apps/meeting-assistant/` on first run; a project's `.vexa/app/` overrides it. Workspace pages are written by Claude through the server, or under `~/Vexa` when the person chose "this computer".
- **Telemetry:** none in the plugin; hosted Vexa's server counts sign-ups and tool calls (vexa.ai privacy policy); a company's own Vexa keeps everything on its server.

**Hosted Vexa (api.cloud.vexa.ai) has no sign-in yet:** until it ships, create an API key at https://vexa.ai/account and put it in `~/.claude/settings.json` as `{ "env": { "VEXA_API_KEY": "…" } }`. Leave it unset on a Vexa that signs you in.

Needs Claude Code **2.1.299 or newer**. Run `claude update` first.

Install from this repo:

```
/plugin marketplace add Vexa-ai/vexa
/plugin install vexa@vexa
```

Then talk to Claude: it offers «Sign in?», gives you a link, you sign in and press **Allow**, and the Vexa panel opens. On hosted Vexa that is Google or Microsoft, and a new account starts with $2 of free credit.

Your company's own Vexa: your administrators ship this plugin from the company's marketplace with its address; the sign-in is then your company's. Administrators: [`ADMIN.md`](ADMIN.md) covers the company marketplace, what runs where, every network call and the data.

Tests: `claude plugin validate clients/claude-plugin` and `claude plugin test clients/claude-plugin` (`tests/`).

Docs: https://docs.vexa.ai/vexa-mcp
