# `core/flows/eval` — evaluation harnesses for the flows

Programs that run the flows engine against recorded material and score what it produced. They are
not tests: a test asks whether the code did what it was told, and these ask whether the product was
any good. Both answers are needed and they are not the same answer.

| | |
|---|---|
| [`dna/`](dna/README.md) | Fixture replay. Walks a library of recorded meetings through the real flows as a real user, in one workspace and in calendar order, then scores the note, the two mails and the two primed chat openings — mechanically first, then against a truth sidecar. |
| [`adoption/`](adoption/README.md) | The adoption simulator. Generates an org and its meeting graph, drives the real flows through it, and asks a persona per touch whether it earned an action — reporting simulated days to adoption, retention, and which lever moved them. Runs in an isolated flows lane so it can never reach a real recipient. |

`dna/` scores what a meeting note SAYS; `adoption/` scores what an org DOES with it. Neither
substitutes for the other, and a change can move one without moving the other.

## The rules these share

- **Recorded material stays out of this repo.** A harness takes its corpus as a `--fixtures` path
  and ships one synthetic example so it is runnable without the private library.
- **Everything through the product's own interfaces.** A harness drives the MCP and the HTTP APIs a
  person's client would drive. It does not reach into another service's database or container — if
  it needs something no interface offers, that gap is the finding.
- **A score with no artifact behind it is not a score.** Every run writes its mails, notes, turns
  and model proof to a run directory, so any number can be argued with afterwards.
- **A dimension that cannot fail is decoration.** Prefer a conservative check that reports what it
  actually saw over a generous one that is usually right.
- **A touch the product does not send cannot be measured.** If a harness needs an artifact, the
  artifact gets built in the product first — a harness never fabricates the thing it is scoring.
- **The number is relative between revolutions, never a forecast.** These exist to rank changes
  against each other.
