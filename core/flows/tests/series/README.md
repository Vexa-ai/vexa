# tests/series — the meeting-series fixture library

Real public recurring meetings, three consecutive episodes each, with the organizer's own
published notes as ground truth. They exist so the scaffold-inference loop
(`witness/series_run.py`) can be iterated **offline, against real longitudinal meetings**, before
a human meeting is spent on it.

| slug | language | series | ground truth |
|---|---|---|---|
| `nodejs-tsc` | en | Node.js Technical Steering Committee, weekly | per-meeting markdown minutes in [`nodejs/TSC/meetings/`](https://github.com/nodejs/TSC/tree/main/meetings), each binding itself to its own recording, with a named roster and speaker-attributed discussion |
| `magdeburg-stadtrat` | de | Stadtrat der Landeshauptstadt Magdeburg, monthly plenary | approved *Niederschriften* (PDF) in the city's SessionNet Ratsinformationssystem |

Each directory carries its own `README.md` (sources, dates, participants, running threads,
provenance, copyright), a `series.json` manifest, `ep<N>.jsonl` transcripts, and
`ground-truth/ep<N>.md` — distillations **written by us** from the published notes, clearly marked
as derived. The linked notes always win over our distillation.

## Layout

```
<slug>/
  series.json          manifest: language, speakers, source, per-episode video + notes URLs, trims
  ep1.jsonl            one JSON object per line — the transcript fixture
  ground-truth/epN.md  derived "what was actually going on", incl. an ## Entities list
  README.md            provenance, participants, threads, known weaknesses
```

## Fixture shape

One JSON object per line — the same columns the `transcriptions` table carries and
`flows_steps/meeting.py: FIXTURE_LINES` uses:

```json
{"start": 6.43, "end": 19.47, "speaker": null, "text": "…", "language": "en"}
```

**`speaker` is `null` wherever the source carried no speaker labels**, which is every auto-caption
track in this library. The manifest declares `speakers: "none" | "labelled"` and
`tests/test_series_fixtures.py` fails the build if a fixture ever gains a label its source did not
have. An invented speaker would be the harness lying to the behavior it exists to test.

## Rebuilding

`fetch.py` is how these were made, kept here so provenance is reproducible rather than asserted:

    python3 fetch.py --url https://www.youtube.com/watch?v=XXXX --lang en --out <slug>/ep1.jsonl --trim-min 40

It runs `yt-dlp --write-auto-sub --skip-download` — **captions only; no audio or video is ever
downloaded** — then converts WebVTT into the fixture shape, deduping YouTube's rolling caption
window at line level and breaking segments on `>>`, YouTube's own speaker-change marker (used as a
turn boundary, never as a name).

## Copyright

Internal test fixtures drawn from public meetings, trimmed to what testing needs, with every
source cited in the per-series README. **Not for republication.** Trims are recorded in each
manifest and README; where a trim cuts a discussion short, the ground-truth file says so, because
a scaffold is not wrong for missing what is not in the transcript.

## Size

The library is capped at 5 MB by `test_series_fixtures.py` and currently sits around 250 KB. Trim
episodes rather than raising the cap.
