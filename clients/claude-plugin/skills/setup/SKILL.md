---
name: setup
description: Connect Vexa in this chat right after Vexa Live was installed («Install Vexa (code VX-…) … then follow vexa-live:setup»), or whenever the person wants Vexa and its tools aren't in this chat — pair, sign in in their own browser, and continue with their first call, all in this same chat.
---

# Vexa setup, in this chat

1. Call `vexa_setup` with the code from the person's message (`code: "VX-XXXX-XXXX"`; without one,
   call it with no code and it starts a pairing). It answers `setup_url`, `signin_url` and `next`.
2. Open `setup_url` in Claude's browser pane (`preview_start`): the setup panel, which ticks each step
   as it happens.
3. Open `signin_url` in the person's OWN browser with the Bash tool: `open "<signin_url>"` (Windows
   `start "" "<signin_url>"`, Linux `xdg-open "<signin_url>"`). Never in the pane, never pasted into the
   chat. Say one line: «Sign in with Google in the browser window that just opened, then come back.»
4. Wait. Don't poll, don't run other commands: the plugin watches the pairing and puts a message here
   when they're signed in: «[Vexa] Signed in as … — Vexa is connected in this chat». From then on
   Vexa's tools (`whats_waiting`, `request_meeting_bot`, …) are in this chat.
5. Then call `whats_waiting` and do its next step: their first call (it opens meet.new in their browser
   and asks for the link), the bot, the transcript, listening, their first knowledge page.

Speak the person's language. One line per step; the setup panel carries the detail. Never show a key.
`vexa_setup` with `action: "status"` says whether Vexa is connected here.
