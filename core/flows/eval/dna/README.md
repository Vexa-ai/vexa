# `eval/dna` — the fixture replay loop

Replays recorded meetings through the *real* flows, as a real user, and scores what the product
produced. The corpus is private and never ships: the harness takes `--fixtures <dir>` and this
repo carries one synthetic fixture under `fixtures/`.

## What a fixture is

    <date>.transcript.json    {meeting: {title, platform, native_meeting_id, participants},
                               segments: [{t, end, speaker, text}]}
    <date>.truth.yaml         decided / committed / open / present · `unvalidated: true`
                              until a human removes it — only a human removes it

## One revolution

    python replay.py    --fixtures ~/dna-fixtures --rev 1 --uid 68
    python score.py     --run ~/dna-runs/r1
    python scoreboard.py --run ~/dna-runs/r1

`replay.py` walks the fixtures in calendar order **in one workspace**, so knowledge compounds the
way it does for a real person. Per fixture, through the MCP only — no database, no `docker exec`:

    segments -> meeting_seed -> fact_emit(meeting.upcoming)  -> prepare mail
                             -> fact_emit(meeting.completed) -> post_meeting -> note + minutes mail
                             -> the two primed openings (_global/asks/{prep,minutes-review}.md)

`score.py` writes `scores.json`: the mechanical dimensions first (what the run *did*), then one
judge column per fixture (`claude -p --model sonnet`, fixed schema, against the truth sidecar).
A fixture whose truth still carries `unvalidated: true` scores into `judge_unvalidated`, never
into the validated column — same discipline as the raise vault.

`scoreboard.py` appends one row per revolution to `SCOREBOARD.md` in the run dir, naming the
**layer** that changed (meta-software or software). The row's fingerprint is
`hash(fixture set) + line SHA + preset/prompt hashes`; a revolution whose fingerprint matches the
previous one is refused, so the loop is safe to re-run.

## The dimensions

| dimension | what it checks |
|---|---|
| `note_shape` | frontmatter · Decided/Committed/Open · every item attributed · wikilinks resolve · no meta-commentary |
| `transcript_depth` | the note cites material that appears ONLY after the 8,000-char mark — the copy-cap test |
| `prepare_mail` | <=5 lines · exactly one link · the link composes a chat holding the meeting |
| `minutes_mail` | the committed note verbatim in the body · exactly one link |
| `opening_prep` | tells before it asks · exactly one question · never says "paste" |
| `opening_minutes` | under 100 words · from the transcript, not the title · exactly one question |
| `compounding` | prep for meeting N names something from a meeting < N |
| `latency_s` | seed -> minutes mail, in seconds |

Mechanical dimensions are 0..1. `score` is their mean; the judge column is reported beside it and
never folded in while the truth is unvalidated.

## Rules this harness obeys

- **The corpus never enters this repo.** `--fixtures` is a path; `fixtures/` here holds one
  synthetic meeting so the harness is runnable and testable in CI.
- **Everything through the MCP.** The replay opens one MCP session and calls tools. It does not
  touch postgres, redis, or another service's container.
- **A run directory is the evidence.** Every mail, note, opening turn, reaction row and the model
  proof land in `~/dna-runs/r<rev>/` — a score with no artifact behind it is not a score.
