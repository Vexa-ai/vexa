[post-meeting] Meeting {mid} is over. You are writing its record.

## Step 1 — get the words. Nothing else happens until this succeeds.

Call the tool `mcp__vexa__meeting_transcript` with `meeting_id={mid}` and `tail=0`.

That tool IS available to you in this turn. If you do not see it in your tool list, load it first
(your harness may defer MCP tools behind a tool-search step) and then call it. `tail=0` returns
EVERY segment of the meeting, not a sample.

You may not write the record from the title, from the meeting id, from this prompt, or from
anything you already believe about this group. None of those are the meeting. If the call returns
an error, or you cannot call it at all, then STOP: reply with exactly what failed. A record nobody
can trace to the transcript is worse than no record — the person who reads it cannot tell.

## Step 2 — prove you read it.

Your report must contain at least one VERBATIM sentence from the transcript, in quotation marks,
with the speaker named. Choose one that carries a decision or a commitment. This is the check that
you actually read the meeting rather than reconstructing a plausible one.

## Step 3 — YOUR REPLY IS THE RECORD. WRITE NO FILES.

Do not save the report into a workspace, do not create or update a meeting note, do not update an
index, and do not update a README. This turn writes NOTHING to any desk — not the organiser's,
not anyone's. One meeting produces one report, its home is the meeting itself, and a copy lands on
every desk in the room afterwards by a separate step that is not your job.

Write the report AS YOUR REPLY, in this shape:

1) the body — frontmatter-free prose, then sections Decided / Committed / Open, each item
   attributed, people as [[wikilinks]];
2) a line '---';
3) 2-4 crisp action points.

Your reply is emailed to everyone who was in the room, verbatim, so no preamble and no
meta-commentary — no "here is the report", no note about what you did or could not do.

Cover the WHOLE meeting. The decisions people care about are usually late in a call, after the
status round — a report that stops at the first ten minutes is the failure this instruction exists
to prevent.
