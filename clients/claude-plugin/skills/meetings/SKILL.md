---
name: vexa-meetings
description: Work with the user's meetings through the Vexa MCP tools — send a bot to a call, follow the transcript while it runs, and write meetings into their notes (Obsidian, Notion, plain markdown) with speakers and action items. Use whenever the user mentions a meeting, a call, a transcript, meeting notes, or asks what was said.
---

# Vexa meetings

The `vexa` MCP server is connected. Start with `whats_waiting` — it says what the user's Vexa needs right now.

## Send a bot to a call

1. `parse_meeting_link(meeting_url)` → platform + native_meeting_id.
2. `request_meeting_bot(meeting_url)`. A human in the call must admit the bot; expect a delay between `requested` and `active`.
3. `get_meeting_transcript(platform, native_meeting_id)` while the meeting runs. Pass `since_index` to read only what is new. A meeting that is `active` with zero segments usually means the bot is not admitted yet or nobody has spoken.
4. `stop_bot(platform, native_meeting_id)` when done.

Every tool returns `platform` + `native_meeting_id`; feed one tool's output straight into the next.

## Write a meeting into the user's notes

When asked to sync, log, or file a meeting:

- `list_meetings` (filters: status, platform, metadata) to find it; `get_meeting_transcript` for the segments; `get_meeting_participants` is not a tool — participants come from the segments' speaker names.
- Write one note per meeting, dated, in the user's existing format if there is one (look at a recent note first). Include: title, date, participants (from speaker labels), a short summary, decisions, action items with owners, and the transcript or a link to it. Keep speaker labels as they are — do not guess names the transcript does not carry.
- Ask before writing outside the folder the user named.

## Search across meetings

`search_transcripts` returns ranked snippets of what was said, not whole transcripts. Use it for "what did we decide about X", then open the meeting for context.

## Make the data durable

`annotate_meeting` attaches a title and metadata (a CRM id, a ticket, tags). Anything you put there is findable later with `list_meetings(metadata_filter=...)`. If you learn something durable about a meeting, write it back.

## Do not

- Do not call `speak_in_meeting` unless the user asked for that exact thing to be said. It is audible to everyone present and irreversible.
- Do not read a meeting's `meetings_failed`-style counters as reliability; a meeting ends in many states that are not errors.
