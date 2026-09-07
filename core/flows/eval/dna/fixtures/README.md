# `eval/dna/fixtures` — one synthetic meeting

The corpus this harness was built for is private and never enters this repo. `replay.py` takes
`--fixtures <dir>`, so the real library lives outside it; what ships here is **one synthetic
meeting**, written for this purpose, so the harness is runnable, testable and demonstrable with
nothing else installed.

    2026-01-15.transcript.json   {meeting: {...}, segments: [{t, end, speaker, text}]}
    2026-01-15.truth.yaml        decided / committed / open / present

## Why this fixture is shaped the way it is

- **Its truth sidecar is the only one that is true by construction** — transcript and sidecar were
  written together, so it carries `unvalidated: false`. Every sidecar for a *recorded* meeting
  starts `unvalidated: true` and only a human may remove that tag.
- It contains a real decision, a real deferral, two owned commitments with dates, and two items
  left deliberately open — so `note_shape`, `minutes_mail` and the judge all have something to
  find, and a note that invents structure is visibly wrong.
- It is **short on purpose**: the whole transcript fits under the 8,000-character delivery cap, so
  `transcript_depth` returns 1 here. That makes it the harness's positive control — if this fixture
  ever scores 0 on depth, the check itself has broken, not the product.

## Adding your own

Drop a `<date>.transcript.json` and a `<date>.truth.yaml` beside these, or point `--fixtures` at a
directory of them. Files are replayed in filename (calendar) order, into ONE workspace, so a
library reads as a series and later meetings can compound on earlier ones.
