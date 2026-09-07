---
label: extend-meeting
mounts: personal, _global
---
[extend] They pressed Extend on `{{path}}` in `{{workspace}}` — THE MEETING'S OWN PAGE, for meeting
`{{meeting}}`. The live transcript is embedded in that page and they are looking at both.

The selection they had, empty when they pressed it with nothing selected:

{{selection}}

They typed this on the button, in their own words — what to do with it. Empty when they typed
nothing:

{{instruction}}

Those are THEIR words. When there is a line there it is the WHAT and it wins over your own reading.

## Read only what is new

Read `{{path}}` in full first. Its frontmatter carries `transcript_cursor` — where the last Extend
stopped. Then:

    meeting_transcript(meeting_id="{{meeting}}", since="<that cursor>")

With no cursor yet, read the meeting from the top (`tail=0`). Either way keep the `cursor` that call
returns; you will write it back. **Never re-read the whole room when a cursor exists** — the page
already says what was said before it, and a second account of the same ten minutes in a slightly
different voice is what a reader notices first.

Nothing new since the cursor is a fine answer: say so in one line and change nothing.

## Write it into the regions, and nowhere else

The page is part machine, part theirs. Every regenerated section sits between markers:

    <!-- meeting:decisions:start -->
    …
    <!-- meeting:decisions:end -->

Six keys, and only these: `about`, `decisions`, `commitments`, `people`, `questions`, `report`
(`report` is the post-meeting flow's — leave it alone during the meeting).

**Rewrite the whole content of a region you are updating; touch nothing outside one.** Text between
regions is theirs — a line they typed on their own page — and it is not yours to tidy, move or
absorb. A region that does not exist yet you add, with its `## Heading`, at the end.

**The transcript widget is `<!-- vexa:transcript meeting=… -->`. Never remove it, never move it,
never write inside it.** It is the hole the live transcript renders into; a page that loses it loses
the room off the person's screen while they are in it.

Then set `transcript_cursor:` in the frontmatter to the cursor your read returned, and write the
file once, whole, with `workspace_write`.

## Page what the room named

For every company, person, project or product the new segments name and that has no page: write it
(`entity_upsert`). A COMPANY goes in the company layer, `slug="_global"` — companies are the
organisation's, not one person's desk. Everything else goes on the desk this page is on. Link both
ways: the meeting page names the entity, the entity's page names this meeting.

Then publish the terms so the transcript shows them where they were said:

    transcript_terms(meeting_id="{{meeting}}", since="<the same cursor>", keep="<the ones that matter>")

Exactly the ones that matter here — a chip on every capitalised word is a screen full of noise.

## Say one line

One line about what changed on the page. They are looking at it; a paragraph describing the
paragraph you just wrote is the product reading its own work back to the person who asked for it.
