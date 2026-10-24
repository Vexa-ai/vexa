---
name: vexa-setup
description: Set Vexa up with the person as a conversation in chat — which Vexa (hosted or the company's own), sign-in, where Claude keeps what it learns (this computer, a cloud workspace, or only when asked), calendar, a first demo. Use when Vexa is not signed in, when setup is unfinished (the session context says so), when the person asks to connect Vexa, a calendar or a workspace, or when the vexa server returns 401.
---

# Vexa setup — in the chat

Setup is a conversation, never a form: check where things stand, ask the ONE next question in plain words, act on the answer with the tools, confirm in one line. The pane only shows status.

## 1. Which Vexa, and signed in?
The plugin talks to the Vexa at `${user_config.vexa_url}` (its MCP server is `${user_config.vexa_url}/mcp`): `https://api.cloud.vexa.ai` is hosted Vexa, the default. A company running its own Vexa sets its address with `/plugin configure vexa@vexa`, the Vexa address row in `/config`, or `claude plugin install vexa@vexa --config vexa_url=https://vexa.example.com`; an administrator can set it for everyone through managed settings (`pluginConfigs`). No trailing slash.

The session context says whether Vexa is connected. If not, tell the person:
«Type /mcp, choose plugin:vexa:vexa, then Authenticate.» Then: on hosted Vexa, «Sign in with Google or Microsoft and press Allow; a new account starts with $2 of free credit»; on a company's Vexa, «your browser opens your company's Vexa: sign in with your company account (Entra, Okta, …) and press Allow».
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
No meeting right now → offer «Try a demo» (`start_demo_meeting`): a presenter speaks for a minute and the transcript appears in the Vexa pane. A real meeting → «send Vexa to <link>» is `request_meeting_bot`.

## Company deployments
What runs where, the network calls and the data, for a company's administrators: the plugin's `ADMIN.md`.

Check the connection any time with `whats_waiting`. A sign-in prompt or 401 means the sign-in hasn't happened or was revoked (that Vexa's account page → Connected apps); a connection error means the address is wrong or unreachable from this machine (VPN, proxy).
