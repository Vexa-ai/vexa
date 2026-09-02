[post-meeting] Meeting {mid} is over. You are writing its record.

## Step 1 — get the words. Nothing else happens until this succeeds.

Call the tool `mcp__vexa__meeting_transcript` with `meeting_id={mid}` and `tail=0`.

That tool IS available to you in this turn. If you do not see it in your tool list, load it first
(your harness may defer MCP tools behind a tool-search step) and then call it. `tail=0` returns
EVERY segment of the meeting, not a sample.

You may not write the note from the title, from the meeting id, from this prompt, or from anything
you already believe about this group. None of those are the meeting. If the call returns an error,
or you cannot call it at all, then STOP: reply with exactly what failed and write no files. A note
nobody can trace to the transcript is worse than no note — the person who reads it cannot tell.

## Step 2 — prove you read it.

Your note must contain at least one VERBATIM sentence from the transcript, in quotation marks, with
the speaker named. Choose one that carries a decision or a commitment. This is the check that you
actually read the meeting rather than reconstructing a plausible one.

## Step 3 — write it.

1) the meeting note at kg/entities/meeting/{date}-{native}.md — frontmatter, then sections
   Decided / Committed / Open, each item attributed, people as [[wikilinks]]; update the index;
2) update README.md as the dashboard;
3) end your reply with the note body EXACTLY as written, then a line '---', then 2-4 crisp action
   points. Your reply is emailed to the participants verbatim, so no meta-commentary.

Cover the WHOLE meeting. The decisions people care about are usually late in a call, after the
status round — a note that stops at the first ten minutes is the failure this instruction exists to
prevent.
