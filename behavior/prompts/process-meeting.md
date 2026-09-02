[post-meeting] The meeting (id {mid}) completed.

FIRST, read the meeting. Call `mcp__vexa__meeting_transcript` with meeting_id={mid} and tail=0.
That returns EVERY segment — the whole meeting, not a sample. Read all of it before you write a
word. If the read fails, say the read failed and stop; do not write a note from the title.

Then do ALL of:
1) write the meeting note at kg/entities/meeting/{date}-{native}.md — frontmatter, then sections
   Decided / Committed / Open, each item attributed, people as [[wikilinks]]; update the index;
2) update README.md as the dashboard;
3) end your reply with the note body EXACTLY as written, then a line '---', then 2-4 crisp action
   points. Your reply's text is emailed to the participants verbatim, so no meta-commentary.

Cover the WHOLE meeting. The decisions people care about are usually late in a call, after the
status round — a note that stops at the first ten minutes is the failure this instruction exists
to prevent.
