---
name: vexa-listen
description: Listen to the person's live call in the background with Vexa Live, answer when someone in the call says "Claude", and keep the meeting's page and its people, companies and decisions current while the call runs; open their Vexa call panel. Use when a Vexa meeting of theirs is live (whats_waiting says a meeting is happening, or a bot they sent just went active), unless they said not to listen, and when they say «Open my Vexa panel».
---

# Listening to a live call (Vexa Live)

Listening is on by default: when the person's Vexa bot is in a live call, start it without asking,
say one line («Listening to <call> — say "Claude" in the call and I'll answer here»), and keep it
running until the call ends or they say stop.

1. Call Vexa's `listen_token` (no arguments). It answers everything the listener needs: `key`,
   `api_url`, and `meeting` (`platform`, `native_meeting_id` of the call a bot of theirs is in now;
   `meetings` lists every live one). Take these from that answer only; don't look for the address
   anywhere else. `meeting` null means no bot is in a call: say so in one line and stop.
2. Start the listener with the Bash tool, `run_in_background: true`:

   ```
   VEXA_LISTEN_KEY=<key> sh "${CLAUDE_PLUGIN_ROOT}/bin/vexa-listen" <api_url> <platform> <native_meeting_id> <since_index>
   ```

   `since_index` is 0 the first time.
3. Open the call panel beside the chat: call Vexa's `panel_link` with
   `next` = `/panel/meeting/<platform>/<native_meeting_id>?surface=claude-panel` and open the link it
   returns in Claude's browser pane (`preview_start`) if this app has one; otherwise give the person
   the link. The link signs the panel in, so do this every time listening starts. «Open my Vexa panel»
   means the same: `panel_link` (next `/panel?surface=claude-panel` without a call), then open it.
4. When it finishes, read its output:
   - `addressed <next_index>` and lines: someone said "Claude" to you. Answer them in the chat, briefly,
     reading `get_meeting_transcript` for context if you need it. Then start it again with that
     `next_index`.
   - `noted <next_index>` and `[kinds] speaker: text` lines: something worth keeping was said — a
     decision, a commitment, a price, a date, or a new person, company or project. Keep the record
     current, silently (no chat message unless you were addressed):
     1. `entity_upsert` each person, company, project and decision the lines name (one call each,
        `source` = this meeting), linking them with `[[Name]]` in `facts` and `connections`.
     2. Update the meeting's page: find it with `get_meeting` (its attached page); if it has none,
        write `meetings/<YYYY-MM-DD>-<title>.md` with `workspace_write` and attach it with
        `meeting_attach_page`. Read it, then rewrite only what sits between these markers:
        `<!-- meeting:decisions:start/end -->`, `<!-- meeting:commitments:start/end -->` (who, what,
        by when), `<!-- meeting:people:start/end -->` (with `[[links]]`) and
        `<!-- meeting:questions:start/end -->`. Everything else stays byte for byte: above all the
        person's own block from `<!-- vexa:human-notes:start -->` to `<!-- vexa:human-notes:end -->`,
        the transcript slot and the `report` region.
     3. Start the listener again with that `next_index`. Say nothing in the chat.
   - `ended <next_index>`: the call is over. Offer the summary and action items in one line.
   - `expired`: call `listen_token` again and restart from where you were.
   - `unreachable`: say once that listening paused; try again on the person's next message.

Never show the key. Don't narrate restarts. One listener per call: don't start a second one while one
is running.
