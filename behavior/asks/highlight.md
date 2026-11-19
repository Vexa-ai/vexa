---
label: highlight
mounts: personal, _global
---
[highlight] They pressed Highlight on the transcript of meeting {{meeting}}. This is MACHINERY: they
are not asking you a question and they will not see a reply. Do not write to them.

1. Call `transcript_terms(meeting_id="{{meeting}}", since="{{since}}")`. It returns every proper name
   the room has said since that cursor, each with whether a page for it already exists.
2. Decide which of them are worth putting on the person's screen — the ones that matter to THIS
   person in THIS meeting. A company in the deal, a person nobody holds a page on, a product or a
   project that was named as a thing to do. Not every capitalised word: not a greeting, not a day of
   the week, not a place mentioned in passing, not a term already chipped before this cursor.
3. Publish exactly those: call it again with `keep="<term>, <term>"`. That call is what paints them.
   Use `keep="*"` only when genuinely all of them matter.
4. If none of them do, publish nothing and stop. An empty Highlight is a correct answer.

Then stop. No message, no summary, no "I've highlighted five terms". The chips ARE the output, and a
sentence about them is the product narrating its own plumbing to somebody who pressed a button.
