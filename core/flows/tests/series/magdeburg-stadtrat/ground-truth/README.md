# ground-truth — magdeburg-stadtrat

One file per episode: `ep<N>.md`, a distillation of **what was actually going on** in that
episode, written by us from the organizer's published notes linked in `../README.md` and in
`../series.json`.

**These are DERIVED, not the minutes.** Each file says so in its own header and links its source.
Where the distillation and the published notes disagree, the notes win.

Each file ends with an `## Entities` section — a bullet list of the people, projects and
vocabulary a correct scaffold has to know about after that episode. `series_run.py judge` reads
exactly that list for its presence check, which is a substring test and **not a score**.

Each file also states the **scope** it was written to: the fixtures are trimmed, so a ground-truth
file records where the transcript stops. A scaffold is not wrong for missing what is not in the
transcript.
