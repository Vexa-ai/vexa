---
name: vexa-setup
description: Set Vexa up with the person as a conversation in chat — which Vexa (hosted or the company's own), sign-in, where Claude keeps what it learns (this computer, a cloud workspace, or only when asked), calendar, a first demo. Use when Vexa is not signed in, when setup is unfinished (the session context says so), when the person asks to connect Vexa, a calendar or a workspace, or when the vexa server returns 401.
---

# Vexa setup — in the chat

Setup is a conversation, never a form: check where things stand, ask the ONE next question in plain words, act on the answer with the tools, confirm in one line. The plugin has no screens: everything happens here in the chat.

## 1. Which Vexa, and signed in?
The plugin talks to one Vexa, fixed in its manifest: hosted Vexa, or the company's own when the company ships the plugin from its own marketplace (`ADMIN.md`). There are no plugin options to configure.

The session context says whether Vexa is connected, and how sign-in works in this session. If not connected, say one line: «Vexa is installed. Sign in?» On yes:
- **Terminal (Claude Code CLI):** call `mcp__plugin_vexa_vexa__authenticate`, write the full link it returns in your reply and say «open it, press Allow, then come back». If the page after Allow shows an error, ask them to paste its address and pass it to `mcp__plugin_vexa_vexa__complete_authentication`. Never run `claude mcp login`.
- **Claude desktop app** (no `authenticate` tool in this session): say one line, «Type /mcp here, choose plugin:vexa:vexa and sign in; your browser opens, press Allow, then come back.» Don't search for a sign-in tool.

What they'll see: on hosted Vexa, «sign in with Google or Microsoft and press Allow; a new account starts with $2 of free credit»; on a company's Vexa, «sign in with your company account (Entra, Okta, …) and press Allow». When it finishes, the plugin notices and opens the Vexa panel; confirm in one line and go to step 2. Never send the person to `/reload-plugins` or plugin settings, and never explain install notices.
**API keys:** never offer one on a Vexa that signs in. Only when the session context itself says this Vexa has no sign-in does the person need a key from https://vexa.ai/account, set as `VEXA_API_KEY` in the `env` block of `~/.claude/settings.json`; never take it in the chat.

Nothing else works until then. Never ask for or accept an API key in the chat.

## 2. Where should I keep what I learn?
Ask once, in these words or close to them:
«Where should I keep what I learn from your calls: on this computer, or in a cloud workspace you can share with your team? Or only when you ask?»
- **Cloud** → `workspace_list`; if they named one, use its slug; if they want a new one, `workspace_new(name)`. Then `vexa_settings(write: "cloud", workspace: <slug>)` — this also sets the account's «Writes to …» default, which the web shows too.
- **This computer** → `vexa_settings(write: "local")` (folder `~/Vexa`, or the folder they name as `local_path`). Moving to Cloud later is one more `vexa_settings` call; offer to copy the local pages into the workspace with `workspace_write`.
- **Only when I ask** → `vexa_settings(write: "ask")`.
Nothing is written anywhere before this answer.

## 3. Calendar (when they want Vexa to join meetings by itself)
`calendar_status` shows connected calendars and which upcoming meetings Vexa joins. To connect one, `calendar_connect` gives the page to open; they sign in there themselves. Then «join only calls I accept», «join all», «skip this one» are said to you and done with `set_auto_join`.

## 4. First value
No meeting right now → offer «Try a demo» (`start_demo_meeting`): a presenter speaks for a minute and the transcript appears in the Vexa panel beside the chat. A real meeting → «send Vexa to <link>» is `request_meeting_bot`.

## Company deployments
What runs where, the network calls and the data, for a company's administrators: the plugin's `ADMIN.md`.

Check the connection any time with `whats_waiting`. A sign-in prompt or 401 means the sign-in hasn't happened or was revoked (that Vexa's account page → Connected apps); a connection error means the address is wrong or unreachable from this machine (VPN, proxy).
