---
name: vexa-listen
description: Keep the line from the person's Vexa open in this chat (vexa-link) and listen to their live calls with a listener profile set for each call — answer when someone in the call says "Claude" or something the profile wakes on, act on presses in the Vexa panel, keep the meeting's page and its people, companies and decisions current. Use on the first Vexa use in a chat, when a Vexa meeting of theirs is live, when they ask to listen or change what the listener reacts to («also wake me on prices», «just take notes»), and when they say «Open my Vexa panel».
---

# Vexa Live: the line from Vexa, and listening

One background script, vexa-link, carries everything Vexa has for this chat: each line said in
the person's live calls, a call starting or ending, a press in the Vexa panel. The Vexa Live plugin
reads it and decides what reaches you, by the listener profile in use. Listening is on by default.

0. **First run, before Vexa is connected** (no Vexa tools in this chat, or they ask to sign in):
   run `sh "${CLAUDE_PLUGIN_ROOT}/bin/vexa-pair"` and open the address it prints in Claude's
   browser pane at once (`preview_start`). That is the setup panel: it shows the person each step,
   the connector's clicks included, and ticks them as they happen. Say one line per step in the
   chat; the panel carries the detail. Once Vexa is connected, pass the code (the `pair=` part of
   that address, also in `~/.vexa/pair`) to `listen_token` as `pair` in step 2: that signs the panel in.
1. **Open the Vexa panel first.** Call Vexa's `panel_link` with `next` =
   `/panel/meeting/<platform>/<native_meeting_id>?surface=claude-panel` for a call, or
   `/panel?surface=claude-panel`, and open the link in Claude's browser pane (`preview_start`); where
   there is none, give the person the link. Any Vexa answer's `open_panel` names the exact `next`.
   Opening it again with the same `next` only refreshes it. «Open my Vexa panel» is this step alone.
2. **Start vexa-link once per chat.** Call Vexa's `listen_token` (with `pair` on a first run): it answers `key`
   and `api_url`; take them from that answer only. Then, with the Bash tool and
   `run_in_background: true`:

   ```
   VEXA_LISTEN_KEY=<key> sh "${CLAUDE_PLUGIN_ROOT}/bin/vexa-link" <api_url>
   ```

   It answers `running <pid>` at once when one already runs: fine, leave it. It keeps running and
   prints only `expired` (call `listen_token` and start it again) or `unreachable` (say once that
   the line to Vexa paused; try again on the person's next message). Never show the key.
3. **Set the profile for this call.** A profile is a file of the person's own: what wakes you
   (being addressed, keywords, people, labels the small model judges), what to ignore, and what you
   do on a wake. Look at the call (its title, who is in it, the calendar event, what the person
   said) and pick the closest profile with `listener_configure` (`preset`: default, sales, standup,
   interview, or one they made), adapting it when the call needs it (`rules`, `triggers`, `labels`,
   `known`; `profile` saves it under a new name). Say one line:
   «Listening to <call> · <profile> profile — say "Claude" in the call and I'll answer here».
   When they ask to change it («also wake me on prices», «stop answering, just take notes»), update
   it with `listener_configure`: the listener uses it from the next line on.

What reaches you, and what to do:
- `[Vexa listener] …` with lines from the call: someone addressed you or the profile woke on it.
  Answer briefly in the chat, reading `get_meeting_transcript` for context if you need it — or, if
  the profile says notes only, update the meeting's page (below) and say nothing.
- `[Vexa listener · signal] …`: something may matter. Look only if it does; otherwise let it pass
  with a single «·».
- `Vexa UI: the person pressed «…» on <call>.` with a `vexa-ui:v1` block: do what the action asks
  (the words under the block), for the meeting and page it names, then answer in one line.
- `[Vexa] The call … ended.`: offer the summary and action items in one line.

The meeting's page, kept current silently while the call runs (`vexa-live:workspace` has the rules):
`entity_upsert` each person, company, project and decision named, linked with `[[Name]]`; on the
meeting's page (found with `get_meeting`; created with `workspace_write` and attached with
`meeting_attach_page` when it has none) rewrite only the decisions, commitments, people and open
questions regions; the person's block from `<!-- vexa:human-notes:start -->` to
`<!-- vexa:human-notes:end -->`, the transcript slot and the report stay byte for byte.

Name the call. Once its topic is clear (the first two minutes or so of talk, or the calendar event's
title), give it a short title with `annotate_meeting` (`title`, six words at most: the topic, never
«Call with …»). Rename it later only if the topic clearly changed and the person hasn't renamed it.

Without vexa-link (an older setup), `bin/vexa-listen` follows one call: start it per live call with
`<api_url> <platform> <native_meeting_id> <since_index> "<title>"`; it exits with `addressed`,
`noted`, `ended`, `expired` or `unreachable`, and is started again with the `next_index` it printed.
