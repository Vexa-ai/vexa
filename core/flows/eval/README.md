# `core/flows/eval` — how the flows product is measured

Harnesses that score what the engine actually produces, against real mail, real notes and a
real agent. They are evaluation code: nothing here runs in production, and nothing here is
imported by a step.

| | |
|---|---|
| [`adoption/`](adoption/README.md) | the **adoption simulator** — simulated days to full adoption of a generated org, paired with retention. The product is real; only the people are simulated. Runs in its own isolated flows lane so it can never reach a real recipient. |

Planned neighbours (not yet here): `dna/` — the fixture replay + truth scorer for minutes
quality, which scores what a meeting note SAYS rather than what the org DOES with it.

Two rules hold for anything added under this directory:

- **A touch the product does not send cannot be measured.** If a harness needs an artifact, the
  artifact gets built in the product first — the simulator does not fabricate the thing it is
  scoring.
- **The number is relative between revolutions, never a forecast.** These harnesses exist to
  rank changes against each other.
