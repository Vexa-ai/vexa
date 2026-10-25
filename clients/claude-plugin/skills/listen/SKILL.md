---
name: vexa-listen
description: Listen to the person's live call in the background with Vexa Live, answer when someone in the call says "Claude", and keep the meeting's page and its people, companies and decisions current while the call runs; open their Vexa call panel. Use when a Vexa meeting of theirs is live (whats_waiting says a meeting is happening, or a bot they sent just went active), unless they said not to listen, and when they say «Open my Vexa panel».
---

# Listening to a live call (Vexa Live)

Listening is on by default: when the person's Vexa bot is in a live call, start it without asking
and keep it running until the call ends or they say stop. The panel opens first: until it is open
the person has nothing to look at.

1. Open the Vexa panel before anything else: call Vexa's `panel_link` with `next` =
   `/panel/meeting/<platform>/<native_meeting_id>?surface=claude-panel` (the call you are about to
   listen to; `/panel?surface=claude-panel` when you don't know it yet) and open the link it returns
   in Claude's browser pane (`preview_start`); where there is none, give the person the link. Any
   Vexa answer's `open_panel` names the exact `next`. Opening it again with the same `next` only
   refreshes it. «Open my Vexa panel» means this step alone.
2. Call Vexa's `listen_token` (no arguments). It answers everything the listener needs: `key`,
   `api_url`, and `meetings`, every call a bot of theirs is in now (`platform`, `native_meeting_id`;
   `meeting` is the first). Take these from that answer only; don't look for the address anywhere
   else. No live call: say so in one line and stop.
3. Start one listener per live call, each with the Bash tool, `run_in_background: true`:

   ```
   VEXA_LISTEN_KEY=<key> sh "${CLAUDE_PLUGIN_ROOT}/bin/vexa-listen" <api_url> <platform> <native_meeting_id> <since_index> "<title>"
   ```

   `since_index` is 0 the first time; `title` is the call's title, empty until it has one. With more
   than one live call, listen to all of them, never only the first. Then say one line naming them:
   «Listening to <title or id>[, <title or id>] — say "Claude" in the call and I'll answer here».
   The band above the prompt shows the same.
4. When one finishes, read its output (each call's listener on its own):
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
     3. Start that call's listener again with that `next_index`. Say nothing in the chat.
   - `ended <next_index>`: the call is over. Offer the summary and action items in one line.

Name the call. Once its topic is clear (the first two minutes or so of talk, or the calendar event's
title if it came from the calendar), give it a short title with `annotate_meeting` (`title`, six
words at most: the topic, never «Call with …») and pass it to the listener's next start. Rename it
later only if the topic clearly changed and the person hasn't renamed it themselves.
   - `expired`: call `listen_token` again and restart from where you were.
   - `unreachable`: say once that listening paused; try again on the person's next message.

Never show the key. Don't narrate restarts. One listener per call: don't start a second one while one
is running.
