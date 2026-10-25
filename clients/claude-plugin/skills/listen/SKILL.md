---
name: vexa-listen
description: Listen to the person's live call in the background with Vexa Live and answer when someone in the call says "Claude"; open their Vexa call panel. Use when a Vexa meeting of theirs is live (whats_waiting says a meeting is happening, or a bot they sent just went active), unless they said not to listen, and when they say «Open my Vexa panel».
---

# Listening to a live call (Vexa Live)

Listening is on by default: when the person's Vexa bot is in a live call, start it without asking,
say one line («Listening to <call> — say "Claude" in the call and I'll answer here»), and keep it
running until the call ends or they say stop.

1. Get a key: call Vexa's `listen_token` (no arguments); it answers `key` and `expires_at`. The
   `api_url` is the address Vexa's own instructions name («This Vexa's API address, for vexa-listen»).
2. Start the listener with the Bash tool, `run_in_background: true`:

   ```
   VEXA_LISTEN_KEY=<key> sh "${CLAUDE_PLUGIN_ROOT}/bin/vexa-listen" <api_url> <platform> <native_meeting_id> <since_index>
   ```

   `platform` and `native_meeting_id` are the meeting's; `since_index` is 0 the first time.
3. Open the call panel beside the chat: call Vexa's `panel_link` with
   `next` = `/panel/meeting/<platform>/<native_meeting_id>?surface=claude-panel` and open the link it
   returns in Claude's browser pane (`preview_start`) if this app has one; otherwise give the person
   the link. The link signs the panel in, so do this every time listening starts. «Open my Vexa panel»
   means the same: `panel_link` (next `/panel?surface=claude-panel` without a call), then open it.
4. When it finishes, read its output:
   - `addressed <next_index>` and lines: someone said "Claude" to you. Answer them in the chat, briefly,
     reading `get_meeting_transcript` for context if you need it. Then start it again with that
     `next_index`.
   - `ended <next_index>`: the call is over. Offer the summary and action items in one line.
   - `expired`: call `listen_token` again and restart from where you were.
   - `unreachable`: say once that listening paused; try again on the person's next message.

Never show the key. Don't narrate restarts. One listener per call: don't start a second one while one
is running.
