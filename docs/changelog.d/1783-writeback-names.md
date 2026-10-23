- **Fewer, correct page proposals after a turn; the bot is sent only when asked (#1783).** The
  write-back check that proposes pages for new names now reads the agent's whole reply instead of
  its streamed pieces, so it no longer offers cut-off names. It handles names in any alphabet, skips
  code, diagrams, tables and bold UI labels, ignores routine and preset instructions, and offers a
  name at most once per chat session. It no longer offers format placeholders, HTTP status text,
  token examples or sentence starts. During a live meeting, the "send the bot" instruction is added
  only when the person's own message asks to join, send or stop, not when the meeting context
  mentions it. A deployment's unedited copy of an older meeting Extend preset is now updated at
  start, so Extend uses `get_meeting_transcript` with `since_index` and writes company pages where
  the person may write.
